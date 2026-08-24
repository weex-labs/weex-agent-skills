#!/usr/bin/env python3
"""Stable JSON facade for WEEX automated-trading authorization."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from weex_auto_trade_state import (
    AutoTradeState,
    StateConflictError,
    build_verified_usage_evidence,
)
from weex_trade_guard import (
    _validate_official_order_semantics,
    resolve_official_auto_trade_operation,
    submit_authorized_order,
)


STATE_DB_NAME = "authorization-state.sqlite3"
MAX_INPUT_BYTES = 1_048_576
RAW_CREDENTIAL_KEYS = frozenset(
    {
        "apikey",
        "apisecret",
        "secret",
        "passphrase",
        "apipassphrase",
        "password",
        "vaultpassword",
    }
)
SCOPE_FIELDS = frozenset(
    {
        "trade_types",
        "symbols",
        "all_symbols",
        "max_single_amount",
        "max_total_amount",
        "valid_hours",
    }
)
COMMAND_SCHEMAS: dict[str, tuple[set[str], set[str]]] = {
    "register-strategy": ({"profile", "strategy_name"}, {"strategy_id"}),
    "list-strategies": ({"profile"}, {"include_retired"}),
    "retire-strategy": ({"profile", "strategy_id"}, set()),
    "ensure-authorization": (
        {
            "profile",
            "strategy_id",
            "trade_types",
            "symbols",
            "all_symbols",
            "max_single_amount",
            "max_total_amount",
            "valid_hours",
        },
        set(),
    ),
    "show-authorization-request": ({"profile", "strategy_id", "request_id"}, set()),
    "grant-authorization": (
        {"profile", "strategy_id", "request_id", "scope_signature"},
        set(),
    ),
    "list-authorizations": ({"profile"}, {"strategy_id"}),
    "revoke-authorization": ({"profile", "strategy_id", "authorization_id"}, set()),
    "submit-auto": (
        {
            "profile",
            "strategy_id",
            "authorization_id",
            "idempotency_key",
            "operation_key",
            "orders",
        },
        set(),
    ),
    "resolve-auto-usage": (
        {"profile", "strategy_id", "usage_id"},
        set(),
    ),
    "enable-auto-trading-after-restore": ({"profile"}, set()),
    "reconcile-auto-order": (
        {"profile", "strategy_id", "auto_trade_order_id"},
        set(),
    ),
    "event-list": ({"profile", "strategy_id"}, set()),
    "snapshot-state": ({"profile"}, {"retention_count"}),
    "restore-state": ({"profile", "snapshot_id"}, set()),
}


class FacadeError(ValueError):
    def __init__(self, code: str, message: str, next_action: str) -> None:
        super().__init__(message)
        self.code = code
        self.next_action = next_action


def state_db_path() -> Path:
    raw_home = os.getenv("WEEX_TRADER_SKILL_HOME")
    config_home = Path(raw_home).expanduser() if raw_home else Path.home() / ".weex-trader-skill"
    return config_home / "auto-trade" / STATE_DB_NAME


class AutoTradeFacade:
    """The only supported consumer boundary for local authorization state."""

    def __init__(
        self,
        state: AutoTradeState,
        *,
        profile_resolver: Callable[[str], Any],
        auto_trade_runtime_factory: Callable[[Any], Any] | None = None,
        reconciliation_provider: Callable[[dict[str, Any], Any], dict[str, Any]] | None = None,
        usage_resolution_provider: Callable[[dict[str, Any], Any], dict[str, Any]] | None = None,
        manual_intent_writer: Callable[[dict[str, Any]], Any] | None = None,
        notification_adapter: Callable[[dict[str, Any]], Any] | None = None,
        notification_worker_launcher: Callable[..., Any] | None = None,
    ) -> None:
        self.state = state
        self.profile_resolver = profile_resolver
        self.auto_trade_runtime_factory = auto_trade_runtime_factory
        self.reconciliation_provider = reconciliation_provider
        self.usage_resolution_provider = usage_resolution_provider
        self.manual_intent_writer = manual_intent_writer
        self.notification_adapter = notification_adapter
        self.notification_worker_launcher = notification_worker_launcher

    def execute(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        _reject_raw_credentials(payload)
        handler = {
            "register-strategy": self._register_strategy,
            "list-strategies": self._list_strategies,
            "retire-strategy": self._retire_strategy,
            "ensure-authorization": self._ensure_authorization,
            "show-authorization-request": self._show_authorization_request,
            "grant-authorization": self._grant_authorization,
            "list-authorizations": self._list_authorizations,
            "revoke-authorization": self._revoke_authorization,
            "submit-auto": self._submit_auto,
            "resolve-auto-usage": self._resolve_auto_usage,
            "enable-auto-trading-after-restore": self._enable_auto_trading_after_restore,
            "reconcile-auto-order": self._reconcile_auto_order,
            "event-list": self._event_list,
            "snapshot-state": self._snapshot_state,
            "restore-state": self._restore_state,
        }.get(command)
        if handler is None:
            raise FacadeError("UNSUPPORTED_COMMAND", "unsupported auto-trade command", "CHECK_COMMAND")
        lock_factory = getattr(self.state, "operation_lock", None)
        operation_lock = nullcontext()
        if callable(lock_factory):
            candidate = lock_factory()
            if hasattr(candidate, "__enter__") and hasattr(candidate, "__exit__"):
                operation_lock = candidate
        with operation_lock:
            result = handler(payload, confirm_live=confirm_live)
            self._schedule_accepted_summary_worker(command, payload, result)
        self._dispatch_post_commit_notifications()
        return result

    def _dispatch_post_commit_notifications(self) -> None:
        if self.notification_adapter is None:
            return
        try:
            from weex_auto_trade_notify import dispatch_notification_claims

            dispatch_notification_claims(self.state, self.notification_adapter)
        except Exception:
            # Notification delivery is a post-commit projection, never a business transition.
            return

    def _schedule_accepted_summary_worker(
        self,
        command: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        if self.notification_worker_launcher is None or command != "submit-auto":
            return
        if result.get("next_action") == "INSPECT_EXISTING_USAGE":
            return
        accepted = any(
            isinstance(leg, dict) and leg.get("status") == "ACCEPTED"
            for leg in result.get("legs") or []
        )
        if not accepted:
            return
        try:
            target = self.state.accepted_summary_notification_target(
                strategy_id=_required_text(payload.get("strategy_id"), "strategy_id")
            )
            self.notification_worker_launcher(
                state_path=self.state.db_path,
                notification_key=target["notification_key"],
                not_before=target["not_before"],
            )
        except Exception:
            # Worker launch is best-effort and cannot change a committed order result.
            return

    def _profile(self, payload: dict[str, Any]) -> Any:
        profile_name = _required_text(payload.get("profile"), "profile")
        try:
            profile = self.profile_resolver(profile_name)
        except Exception as exc:
            raise FacadeError("UNKNOWN_PROFILE", "saved profile was not found", "SELECT_SAVED_PROFILE") from exc
        if profile is None:
            raise FacadeError("UNKNOWN_PROFILE", "saved profile was not found", "SELECT_SAVED_PROFILE")
        return profile

    def _assert_strategy_profile(self, strategy_id: str, profile: Any) -> dict[str, str]:
        strategy = self.state.get_strategy(strategy_id=strategy_id)
        if strategy["profile_id"] != profile.profile_id:
            raise FacadeError(
                "STRATEGY_AUTHORIZATION_MISMATCH",
                "strategy does not belong to the selected saved profile",
                "SELECT_MATCHING_PROFILE",
            )
        return strategy

    def _register_strategy(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        _strict_fields(payload, required={"profile", "strategy_name"}, optional={"strategy_id"})
        profile = self._profile(payload)
        result = self.state.register_strategy(
            profile_id=profile.profile_id,
            strategy_name=_required_text(payload["strategy_name"], "strategy_name"),
            distribution="official",
            trading_mode="live",
            strategy_id=payload.get("strategy_id"),
        )
        return _public_strategy(result, profile.name)

    def _list_strategies(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        _strict_fields(payload, required={"profile"}, optional={"include_retired"})
        profile = self._profile(payload)
        include_retired = payload.get("include_retired", True)
        if not isinstance(include_retired, bool):
            raise FacadeError("INVALID_REQUEST", "include_retired must be a boolean", "FIX_REQUEST")
        return {
            "ok": True,
            "profile": profile.name,
            "strategies": [
                _public_strategy(item, profile.name)
                for item in self.state.list_strategies(
                    profile_id=profile.profile_id,
                    include_retired=include_retired,
                )
            ],
        }

    def _retire_strategy(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        _strict_fields(payload, required={"profile", "strategy_id"})
        profile = self._profile(payload)
        strategy_id = _required_text(payload["strategy_id"], "strategy_id")
        self._assert_strategy_profile(strategy_id, profile)
        result = self.state.retire_strategy(strategy_id=strategy_id)
        return _public_strategy(result, profile.name)

    def _ensure_authorization(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        _strict_fields(
            payload,
            required={
                "profile",
                "strategy_id",
                "trade_types",
                "symbols",
                "all_symbols",
                "max_single_amount",
                "max_total_amount",
                "valid_hours",
            },
            optional=set(),
        )
        profile = self._profile(payload)
        strategy_id = _required_text(payload["strategy_id"], "strategy_id")
        self._assert_strategy_profile(strategy_id, profile)
        scope = {key: payload[key] for key in SCOPE_FIELDS if key in payload}
        result = self.state.ensure_authorization(strategy_id=strategy_id, scope=scope)
        return {**result, "profile": profile.name}

    def _show_authorization_request(
        self,
        payload: dict[str, Any],
        *,
        confirm_live: bool,
    ) -> dict[str, Any]:
        _strict_fields(payload, required={"profile", "strategy_id", "request_id"})
        profile = self._profile(payload)
        strategy_id = _required_text(payload["strategy_id"], "strategy_id")
        strategy = self._assert_strategy_profile(strategy_id, profile)
        request = self.state.get_authorization_request(
            strategy_id=strategy_id,
            request_id=_required_text(payload["request_id"], "request_id"),
        )
        request_status = request["request_status"]
        confirmation = {
            "profile": profile.name,
            "trading_mode": "live",
            "strategy_name": strategy["strategy_name"],
            "strategy_id": _mask_identifier(strategy_id),
            "trade_types": request["scope"]["trade_types"],
            "symbols": request["scope"]["symbols"],
            "all_symbols": request["scope"]["all_symbols"],
            "max_single_amount_u": request["scope"]["max_single_amount"],
            "max_total_amount_u": request["scope"]["max_total_amount"],
            "valid_hours": request["scope"]["valid_hours"],
            "request_expires_at": request["request_expires_at"],
            "orders_skip_per_order_confirmation": request_status in {"PENDING", "GRANTED"},
            "revoke_command": "weex_auto_trade.py revoke-authorization --input -",
            "trust_boundary": (
                "Local same-OS-user misuse guard; not identity authentication and not protection "
                "against an attacker controlling the same OS user, Agent, Vault session, or API key."
            ),
        }
        if request_status == "PENDING":
            generated_at = datetime.now(UTC)
            valid_hours = Decimal(request["scope"]["valid_hours"])
            projected_expiry = generated_at + timedelta(
                seconds=int(valid_hours * Decimal(3600))
            )
            confirmation.update(
                {
                    "preview_generated_at": _format_time(generated_at),
                    "projected_expires_at_if_granted_now": _format_time(projected_expiry),
                }
            )
            next_action = "CONFIRM_OR_REJECT_AUTHORIZATION"
        elif request_status == "GRANTED":
            matching = [
                item
                for item in self.state.list_authorizations(strategy_id=strategy_id)
                if item["request_id"] == request["request_id"]
            ]
            if len(matching) != 1:
                raise StateConflictError("granted authorization request has no unique authorization")
            authorization = matching[0]
            confirmation.update(
                {
                    "authorization_id": _mask_identifier(authorization["authorization_id"]),
                    "authorization_status": authorization["status"],
                    "authorization_starts_at": authorization["starts_at"],
                    "authorization_expires_at": authorization["expires_at"],
                    "orders_skip_per_order_confirmation": authorization["status"] == "ACTIVE",
                }
            )
            next_action = authorization["next_action"]
        else:
            next_action = "REQUEST_NEW_AUTHORIZATION"
        return {
            "ok": True,
            "status": request_status,
            "request_id": request["request_id"],
            "scope_signature": request["scope_signature"],
            "confirmation": confirmation,
            "next_action": next_action,
        }

    def _grant_authorization(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        _strict_fields(
            payload,
            required={"profile", "strategy_id", "request_id", "scope_signature"},
        )
        profile = self._profile(payload)
        strategy_id = _required_text(payload["strategy_id"], "strategy_id")
        self._assert_strategy_profile(strategy_id, profile)
        result = self.state.grant_authorization(
            strategy_id=strategy_id,
            request_id=_required_text(payload["request_id"], "request_id"),
            scope_signature=_required_text(payload["scope_signature"], "scope_signature"),
            confirm_live=confirm_live,
        )
        return {**result, "profile": profile.name}

    def _revoke_authorization(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        _strict_fields(payload, required={"profile", "strategy_id", "authorization_id"})
        profile = self._profile(payload)
        strategy_id = _required_text(payload["strategy_id"], "strategy_id")
        self._assert_strategy_profile(strategy_id, profile)
        result = self.state.revoke_authorization(
            strategy_id=strategy_id,
            authorization_id=_required_text(payload["authorization_id"], "authorization_id"),
        )
        return {**result, "profile": profile.name}

    def _list_authorizations(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        _strict_fields(payload, required={"profile"}, optional={"strategy_id"})
        profile = self._profile(payload)
        strategy_id = payload.get("strategy_id")
        if strategy_id is not None:
            strategy_id = _required_text(strategy_id, "strategy_id")
            self._assert_strategy_profile(strategy_id, profile)
        allowed_strategy_ids = {
            strategy["strategy_id"]
            for strategy in self.state.list_strategies(profile_id=profile.profile_id)
        }
        authorizations = [
            item
            for item in self.state.list_authorizations(strategy_id=strategy_id)
            if item["strategy_id"] in allowed_strategy_ids
        ]
        return {
            "ok": True,
            "profile": profile.name,
            "authorizations": [
                {**item, "authorization_id": _mask_identifier(item["authorization_id"])}
                for item in authorizations
            ],
        }

    def _event_list(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        _strict_fields(payload, required={"profile", "strategy_id"})
        profile = self._profile(payload)
        strategy_id = _required_text(payload["strategy_id"], "strategy_id")
        self._assert_strategy_profile(strategy_id, profile)
        events = self.state.list_events(strategy_id=strategy_id)
        authorization_totals = {
            item["authorization_id"]: item["scope"]["max_total_amount"]
            for item in self.state.list_authorizations(strategy_id=strategy_id)
        }
        return {
            "ok": True,
            "profile": profile.name,
            "strategy_id": _mask_identifier(strategy_id),
            "events": [
                _public_event(
                    event,
                    max_total_amount_u=authorization_totals.get(
                        event.get("authorization_id")
                    ),
                )
                for event in events
            ],
        }

    def _submit_auto(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        _strict_fields(payload, required=COMMAND_SCHEMAS["submit-auto"][0])
        profile = self._profile(payload)
        strategy_id = _required_text(payload["strategy_id"], "strategy_id")
        self._assert_strategy_profile(strategy_id, profile)
        authorization_id = _required_text(
            payload["authorization_id"], "authorization_id"
        )
        operation_key = _required_text(payload["operation_key"], "operation_key")
        idempotency_key = _required_text(payload["idempotency_key"], "idempotency_key")
        orders = payload["orders"]
        if not isinstance(orders, list):
            raise FacadeError("INVALID_REQUEST", "orders must be an array", "FIX_REQUEST")

        runtime_factory = self.auto_trade_runtime_factory or _load_auto_trade_runtime_factory()
        try:
            runtime = runtime_factory(profile)
        except FacadeError:
            raise
        except Exception:
            runtime = None
        if runtime is None:
            result = {
                "ok": False,
                "status": "MANUAL_CONFIRMATION_REQUIRED",
                "error": {"code": "RUNTIME_UNAVAILABLE"},
                "advisory_alerts": [],
                "blocking_reasons": [
                    {
                        "code": "RUNTIME_UNAVAILABLE",
                        "message": "official automated-trading runtime is unavailable",
                    }
                ],
                "next_action": "PREVIEW_AND_CONFIRM_ORDER_MANUALLY",
            }
        else:
            try:
                result = submit_authorized_order(
                    state=self.state,
                    operation_key=operation_key,
                    strategy_id=strategy_id,
                    authorization_id=authorization_id,
                    idempotency_key=idempotency_key,
                    orders=orders,
                    risk_payload_provider=runtime.risk_payload_provider,
                    risk_evaluator=runtime.risk_evaluator,
                    facts_provider=runtime.facts_provider,
                    submitter=runtime.submitter,
                    confirm_live=confirm_live,
                )
            except Exception:
                try:
                    self.state.record_submission_state_uncertain(
                        strategy_id=strategy_id,
                        authorization_id=authorization_id,
                        operation_key=operation_key,
                        idempotency_key=idempotency_key,
                    )
                except Exception:
                    try:
                        self.state.disable_automatic_trading(
                            reason="SUBMISSION_STATE_UNCERTAIN"
                        )
                    except Exception:
                        pass
                result = {
                    "ok": False,
                    "status": "REVIEW_REQUIRED",
                    "error": {"code": "SUBMISSION_STATE_UNCERTAIN"},
                    "advisory_alerts": [],
                    "blocking_reasons": [],
                    "next_action": "INSPECT_AND_RECONCILE_MANUALLY",
                }

        if result.get("status") == "MANUAL_CONFIRMATION_REQUIRED":
            error = result.get("error")
            error_code = (
                str(error.get("code") or "HARD_CHECK_FAILED")
                if isinstance(error, dict)
                else "HARD_CHECK_FAILED"
            )
            self.state.record_manual_fallback(
                strategy_id=strategy_id,
                authorization_id=authorization_id,
                operation_key=operation_key,
                idempotency_key=idempotency_key,
                error_code=error_code,
                blocking_reasons=list(result.get("blocking_reasons") or []),
                advisory_alerts=list(result.get("advisory_alerts") or []),
            )
            intent = _build_manual_fallback_intent(
                profile_name=profile.name,
                strategy_id=strategy_id,
                authorization_id=authorization_id,
                idempotency_key=idempotency_key,
                operation_key=operation_key,
                orders=orders,
                guard_result=result,
            )
            if intent is not None:
                writer = self.manual_intent_writer or _save_manual_fallback_intent
                writer(intent)
                authorization_hint = (
                    "如需取消二次确认功能，可申请自动交易授权。授权后，在指定交易类型、交易对、"
                    "单笔金额和有效期范围内，下单无需逐笔确认。发送“申请自动交易授权”即可开始配置。"
                )
                is_authorization_miss = error_code in {
                    "AUTHORIZATION_NOT_ACTIVE",
                    "SCOPE_MISMATCH",
                    "SINGLE_LIMIT_EXCEEDED",
                    "TOTAL_LIMIT_EXCEEDED",
                }
                notice = (
                    "本次订单超过自动交易授权范围，尚未下单。"
                    if is_authorization_miss
                    else "本次订单未进入自动交易执行，尚未下单。"
                )
                confirmation_lines = [
                    notice,
                    "",
                    "请核对 order_preview 中的完整订单。",
                    "",
                    "确认后回复：确认，我直接下单。",
                ]
                if is_authorization_miss:
                    confirmation_lines.extend(["", authorization_hint])
                result = {
                    **result,
                    "intent_id": intent["intent_id"],
                    "expires_at": intent["expires_at"],
                    "risk_signature": intent["risk_signature"],
                    "order_preview": intent["order_preview"],
                    "user_confirmation": {
                        "language": "zh",
                        "reply_text": "确认",
                        "reply_instruction": "\n".join(confirmation_lines),
                    },
                    "next_action": "CONFIRM_ORDER_MANUALLY",
                }
                if is_authorization_miss:
                    result["authorization_hint"] = authorization_hint
        return _public_auto_result(
            result,
            profile_name=profile.name,
            strategy_id=strategy_id,
            authorization_id=authorization_id,
        )

    def _reconcile_auto_order(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        required = COMMAND_SCHEMAS["reconcile-auto-order"][0]
        _strict_fields(payload, required=required)
        profile = self._profile(payload)
        strategy_id = _required_text(payload["strategy_id"], "strategy_id")
        self._assert_strategy_profile(strategy_id, profile)
        auto_trade_order_id = _required_text(
            payload["auto_trade_order_id"], "auto_trade_order_id"
        )
        current_order = self.state.get_order(auto_trade_order_id=auto_trade_order_id)
        if current_order["strategy_id"] != strategy_id:
            raise FacadeError(
                "STRATEGY_AUTHORIZATION_MISMATCH",
                "order does not belong to the selected strategy",
                "SELECT_MATCHING_STRATEGY",
            )
        provider = self.reconciliation_provider or _load_reconciliation_provider()
        try:
            facts = provider(current_order, profile)
        except Exception:
            facts = {
                "reconciliation_status": "UNAVAILABLE",
                "exchange_status": None,
                "executed_quantity": None,
                "executed_quote_amount": None,
                "fee_amount": None,
                "fee_asset": None,
                "reconciliation_source": "WEEX_READ_ONLY_QUERY_UNAVAILABLE",
            }
        required_fact_fields = {
            "reconciliation_status",
            "exchange_status",
            "executed_quantity",
            "executed_quote_amount",
            "fee_amount",
            "fee_asset",
            "reconciliation_source",
        }
        if not isinstance(facts, dict) or set(facts) != required_fact_fields:
            raise FacadeError(
                "RECONCILIATION_FACTS_INVALID",
                "official reconciliation facts are incomplete or invalid",
                "INSPECT_OFFICIAL_QUERY",
            )
        result = self.state.reconcile_order(
            auto_trade_order_id=auto_trade_order_id,
            reconciliation_status=facts["reconciliation_status"],
            exchange_status=facts["exchange_status"],
            executed_quantity=facts["executed_quantity"],
            executed_quote_amount=facts["executed_quote_amount"],
            fee_amount=facts["fee_amount"],
            fee_asset=facts["fee_asset"],
            reconciliation_source=facts["reconciliation_source"],
        )
        return _public_reconciliation_result(result, profile_name=profile.name)

    def _resolve_auto_usage(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        required, optional = COMMAND_SCHEMAS["resolve-auto-usage"]
        _strict_fields(payload, required=required, optional=optional)
        profile = self._profile(payload)
        strategy_id = _required_text(payload["strategy_id"], "strategy_id")
        self._assert_strategy_profile(strategy_id, profile)
        if confirm_live is not True:
            raise FacadeError(
                "LIVE_CONFIRMATION_REQUIRED",
                "resolve-auto-usage requires --confirm-live",
                "CONFIRM_LIVE_RECOVERY",
            )
        usage_id = _required_text(payload["usage_id"], "usage_id")
        try:
            current_order = self.state.get_order_for_usage(usage_id=usage_id)
        except (ValueError, StateConflictError) as exc:
            raise FacadeError(
                _value_error_code(exc) if isinstance(exc, ValueError) else "STATE_CONFLICT",
                str(exc),
                "INSPECT_AUTOMATED_TRADING_STATE",
            ) from exc
        if current_order["strategy_id"] != strategy_id:
            raise FacadeError(
                "STRATEGY_AUTHORIZATION_MISMATCH",
                "usage does not belong to the selected strategy",
                "SELECT_MATCHING_STRATEGY",
            )
        provider = self.usage_resolution_provider or _load_usage_resolution_provider()
        try:
            facts = provider(current_order, profile)
            verified_evidence = build_verified_usage_evidence(facts)
        except Exception as exc:
            if isinstance(exc, FacadeError):
                raise
            if current_order.get("usage_status") == "RESERVED":
                try:
                    self.state.settle_usage(
                        usage_id=usage_id,
                        outcome="REVIEW_REQUIRED",
                    )
                except Exception:
                    # Preserve the reservation if the fail-closed review marker
                    # itself cannot be persisted; never release quota here.
                    pass
            raise FacadeError(
                "RECONCILIATION_FACTS_INVALID",
                "official order query did not provide complete, bound resolution facts",
                "INSPECT_OFFICIAL_QUERY",
            ) from exc
        try:
            result = self.state.resolve_uncertain_usage(
                verified_evidence=verified_evidence,
                confirm_live=confirm_live,
            )
        except (ValueError, StateConflictError) as exc:
            code = _value_error_code(exc) if isinstance(exc, ValueError) else "STATE_CONFLICT"
            raise FacadeError(code, str(exc), "INSPECT_OFFICIAL_QUERY") from exc
        return {
            **_public_usage_amounts(result),
            "profile": profile.name,
            "strategy_id": _mask_identifier(result["strategy_id"]),
            "authorization_id": _mask_identifier(result["authorization_id"]),
        }

    def _enable_auto_trading_after_restore(
        self,
        payload: dict[str, Any],
        *,
        confirm_live: bool,
    ) -> dict[str, Any]:
        _strict_fields(payload, required={"profile"})
        profile = self._profile(payload)
        result = self.state.enable_auto_trading_after_restore(confirm_live=confirm_live)
        return {**result, "profile": profile.name}

    def _snapshot_state(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        _strict_fields(payload, required={"profile"}, optional={"retention_count"})
        profile = self._profile(payload)
        retention_count = payload.get("retention_count", 10)
        if (
            isinstance(retention_count, bool)
            or not isinstance(retention_count, int)
            or not 1 <= retention_count <= 100
        ):
            raise FacadeError(
                "INVALID_RETENTION_COUNT",
                "retention_count must be an integer from 1 through 100",
                "FIX_REQUEST",
            )
        result = self.state.snapshot_state(retention_count=retention_count)
        return {**result, "profile": profile.name}

    def _restore_state(self, payload: dict[str, Any], *, confirm_live: bool) -> dict[str, Any]:
        _strict_fields(payload, required={"profile", "snapshot_id"})
        profile = self._profile(payload)
        result = self.state.restore_state(
            snapshot_id=_required_text(payload["snapshot_id"], "snapshot_id")
        )
        return {**result, "profile": profile.name}


def _load_profile_resolver() -> Callable[[str], Any]:
    try:
        from weex_profile_store import resolve_profile
    except (ImportError, ModuleNotFoundError) as exc:
        raise FacadeError(
            "RUNTIME_UNAVAILABLE",
            "saved profile metadata runtime is unavailable",
            "RUN_RUNTIME_SETUP",
        ) from exc
    return resolve_profile


def _load_auto_trade_runtime_factory() -> Callable[[Any], Any]:
    try:
        from weex_auto_trade_runtime import OfficialAutoTradeRuntime
    except (ImportError, ModuleNotFoundError) as exc:
        raise FacadeError(
            "RUNTIME_UNAVAILABLE",
            "official automated-trading runtime is unavailable",
            "RUN_RUNTIME_SETUP",
        ) from exc
    return lambda profile: OfficialAutoTradeRuntime(profile_name=profile.name)


def _load_reconciliation_provider() -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    try:
        from weex_auto_trade_runtime import query_official_order_facts
    except (ImportError, ModuleNotFoundError) as exc:
        raise FacadeError(
            "RUNTIME_UNAVAILABLE",
            "official order reconciliation runtime is unavailable",
            "RUN_RUNTIME_SETUP",
        ) from exc
    return lambda order, profile: query_official_order_facts(
        order=order,
        profile_name=profile.name,
    )


def _load_usage_resolution_provider() -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    try:
        from weex_auto_trade_runtime import query_official_usage_resolution
    except (ImportError, ModuleNotFoundError) as exc:
        raise FacadeError(
            "RUNTIME_UNAVAILABLE",
            "official order resolution runtime is unavailable",
            "RUN_RUNTIME_SETUP",
        ) from exc
    return lambda order, profile: query_official_usage_resolution(
        order=order,
        profile_name=profile.name,
    )


def _build_manual_fallback_intent(
    *,
    profile_name: str,
    strategy_id: str,
    authorization_id: str,
    idempotency_key: str,
    operation_key: str,
    orders: list[dict[str, Any]],
    guard_result: dict[str, Any],
) -> dict[str, Any] | None:
    from weex_order_intent_state import build_intent

    operation = resolve_official_auto_trade_operation(operation_key)
    if operation is None or not orders or any(not isinstance(item, dict) for item in orders):
        return None
    if len(orders) > operation["max_legs"] or (
        operation["kind"] != "BATCH" and len(orders) != 1
    ):
        return None
    if any(set(order) - operation["allowed_order_fields"] for order in orders):
        return None
    if operation_key == "spot.order.bulk_order" and len(
        {str(order.get("symbol") or "").upper() for order in orders}
    ) != 1:
        return None
    if any(_validate_official_order_semantics(operation, order) for order in orders):
        return None
    market = str(operation["module"]).lower()
    preview = {
        "operation_key": operation_key,
        "market": market,
        "kind": operation["kind"],
        "order_count": len(orders),
        "symbols": sorted(
            {
                str(item.get("symbol") or "").upper()
                for item in orders
                if isinstance(item, dict) and str(item.get("symbol") or "").strip()
            }
        ),
        "orders": [dict(item) for item in orders],
    }
    analysis_output = {
        "alerts": list(guard_result.get("advisory_alerts") or []),
        "blocking_reasons": list(guard_result.get("blocking_reasons") or []),
    }
    intent = build_intent(
        profile_name=profile_name,
        market=market,
        trading_mode="live",
        order_preview=preview,
        raw_order=dict(orders[0]),
        analysis_output=analysis_output,
        now_ms=int(time.time() * 1000),
    )
    intent.update(
        {
            "strategy_id": strategy_id,
            "authorization_id": authorization_id,
            "idempotency_key": idempotency_key,
            "operation_key": operation_key,
            "auto_fallback_operation_key": operation_key,
            "auto_fallback_orders": [dict(item) for item in orders],
        }
    )
    return intent


def _save_manual_fallback_intent(intent: dict[str, Any]) -> None:
    from weex_order_intent_state import save_intent

    save_intent(intent)


def _public_auto_result(
    result: dict[str, Any],
    *,
    profile_name: str,
    strategy_id: str,
    authorization_id: str,
) -> dict[str, Any]:
    public_legs = []
    for leg in result.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        public_leg = _public_usage_amounts(leg)
        public_legs.append(
            {
                **public_leg,
                "strategy_id": _mask_identifier(leg.get("strategy_id")),
                "authorization_id": _mask_identifier(leg.get("authorization_id")),
            }
        )
    return {
        **result,
        "profile": profile_name,
        "strategy_id": _mask_identifier(strategy_id),
        "authorization_id": _mask_identifier(authorization_id),
        "legs": public_legs,
    }


def _public_reconciliation_result(
    result: dict[str, Any],
    *,
    profile_name: str,
) -> dict[str, Any]:
    public = dict(result)
    authorization_quota = {
        "consumed_amount_u": public.pop("accepted_amount_u"),
        "reserved_amount_u": public.pop("reserved_amount_u"),
        "remaining_amount_u": public.pop("remaining_amount_u"),
    }
    return {
        **public,
        "profile": profile_name,
        "authorization_id": _mask_identifier(result["authorization_id"]),
        "authorization_quota": authorization_quota,
    }


def _public_strategy(result: dict[str, str], profile_name: str) -> dict[str, Any]:
    return {
        "ok": True,
        "strategy_id": result["strategy_id"],
        "strategy_name": result["strategy_name"],
        "status": result["status"],
        "profile": profile_name,
        "distribution": result["distribution"],
        "trading_mode": result["trading_mode"],
        "created_at": result["created_at"],
        "updated_at": result["updated_at"],
    }


def _public_event(
    event: dict[str, Any],
    *,
    max_total_amount_u: str | None = None,
) -> dict[str, Any]:
    payload = dict(event.get("payload") or {})
    if "accepted_amount_u" in payload or "reserved_amount_u" in payload:
        consumed = payload.pop("accepted_amount_u", None)
        reserved = payload.pop("reserved_amount_u", None)
        quota: dict[str, Any] = {
            "consumed_amount_u": consumed,
            "reserved_amount_u": reserved,
        }
        if consumed is not None and reserved is not None and max_total_amount_u is not None:
            quota["remaining_amount_u"] = _decimal_text(
                Decimal(max_total_amount_u) - Decimal(consumed) - Decimal(reserved)
            )
        payload["authorization_quota"] = quota
    return {
        **event,
        "strategy_id": _mask_identifier(event["strategy_id"]),
        "authorization_id": _mask_identifier(event["authorization_id"]),
        "payload": payload,
    }


def _public_usage_amounts(result: dict[str, Any]) -> dict[str, Any]:
    public = dict(result)
    keys = ("accepted_amount_u", "reserved_amount_u", "remaining_amount_u")
    if all(key in public for key in keys):
        public["authorization_quota"] = {
            "consumed_amount_u": public.pop("accepted_amount_u"),
            "reserved_amount_u": public.pop("reserved_amount_u"),
            "remaining_amount_u": public.pop("remaining_amount_u"),
        }
    return public


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _mask_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    prefix = value.split("_", 1)[0] + "_" if "_" in value else "id_"
    return prefix + "***" + value[-6:]


def _reject_raw_credentials(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = "".join(character for character in str(key).lower() if character.isalnum())
            if normalized in RAW_CREDENTIAL_KEYS:
                raise FacadeError(
                    "RAW_CREDENTIALS_NOT_ALLOWED",
                    "raw credentials are not accepted by automated-trading commands",
                    "USE_SAVED_PROFILE",
                )
            _reject_raw_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _reject_raw_credentials(child)


def _strict_fields(
    payload: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = required - set(payload)
    unknown = set(payload) - required - optional
    if missing:
        raise FacadeError(
            "INVALID_REQUEST",
            "missing required fields: " + ", ".join(sorted(missing)),
            "FIX_REQUEST",
        )
    if unknown:
        raise FacadeError(
            "INVALID_REQUEST",
            "unknown fields: " + ", ".join(sorted(unknown)),
            "FIX_REQUEST",
        )


def _validate_command_payload(command: str, payload: dict[str, Any]) -> None:
    schema = COMMAND_SCHEMAS.get(command)
    if schema is None:
        raise FacadeError("UNSUPPORTED_COMMAND", "unsupported auto-trade command", "CHECK_COMMAND")
    required, optional = schema
    _strict_fields(payload, required=required, optional=optional)


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FacadeError("INVALID_REQUEST", f"{field} must be a non-empty string", "FIX_REQUEST")
    return value.strip()


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _load_input(source: str) -> dict[str, Any]:
    if source == "-":
        raw = sys.stdin.read(MAX_INPUT_BYTES + 1)
    else:
        path = Path(source[1:] if source.startswith("@") else source)
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise FacadeError("INVALID_REQUEST", "input JSON is too large", "FIX_REQUEST")
        with path.open("rb") as input_file:
            encoded = input_file.read(MAX_INPUT_BYTES + 1)
        if len(encoded) > MAX_INPUT_BYTES:
            raise FacadeError("INVALID_REQUEST", "input JSON is too large", "FIX_REQUEST")
        raw = encoded.decode("utf-8")
    if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
        raise FacadeError("INVALID_REQUEST", "input JSON is too large", "FIX_REQUEST")
    if not raw.strip():
        raise FacadeError("INVALID_REQUEST", "input JSON is empty", "FIX_REQUEST")

    def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FacadeError("INVALID_REQUEST", "input JSON contains duplicate keys", "FIX_REQUEST")
            result[key] = value
        return result

    try:
        payload = json.loads(raw, object_pairs_hook=no_duplicate_keys)
    except FacadeError:
        raise
    except json.JSONDecodeError as exc:
        raise FacadeError("INVALID_REQUEST", "input is not valid JSON", "FIX_REQUEST") from exc
    if not isinstance(payload, dict):
        raise FacadeError("INVALID_REQUEST", "input JSON must be an object", "FIX_REQUEST")
    return payload


def _error_payload(code: str, message: str, next_action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message},
        "next_action": next_action,
    }


def _value_error_code(exc: ValueError) -> str:
    message = str(exc)
    if message and all(character.isupper() or character.isdigit() or character == "_" for character in message):
        return message
    return "INVALID_REQUEST"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WEEX automated-trading authorization JSON facade")
    parser.add_argument(
        "command",
        choices=(
            "register-strategy",
            "list-strategies",
            "retire-strategy",
            "ensure-authorization",
            "show-authorization-request",
            "grant-authorization",
            "list-authorizations",
            "revoke-authorization",
            "submit-auto",
            "resolve-auto-usage",
            "enable-auto-trading-after-restore",
            "reconcile-auto-order",
            "event-list",
            "snapshot-state",
            "restore-state",
        ),
    )
    parser.add_argument("--input", default="-", help="JSON request path, @path, or - for stdin")
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required for live authorization, automatic submission, and recovery transitions",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _load_input(args.input)
        _reject_raw_credentials(payload)
        _validate_command_payload(args.command, payload)
        profile_resolver = _load_profile_resolver()
        try:
            resolved_profile = profile_resolver(_required_text(payload.get("profile"), "profile"))
        except FacadeError:
            raise
        except Exception as exc:
            raise FacadeError(
                "UNKNOWN_PROFILE",
                "saved profile was not found",
                "SELECT_SAVED_PROFILE",
            ) from exc
        if resolved_profile is None:
            raise FacadeError(
                "UNKNOWN_PROFILE",
                "saved profile was not found",
                "SELECT_SAVED_PROFILE",
            )
        state = AutoTradeState(state_db_path())
        notification_adapter = None
        notification_worker_launcher = None
        if os.getenv("WEEX_AUTO_TRADE_NOTIFICATION_MODE", "system") == "system":
            try:
                from weex_auto_trade_notify import (
                    SystemNotificationAdapter,
                    launch_notification_worker,
                )

                notification_adapter = SystemNotificationAdapter()
                notification_worker_launcher = launch_notification_worker
            except Exception:
                notification_adapter = None
                notification_worker_launcher = None
        facade = AutoTradeFacade(
            state,
            profile_resolver=profile_resolver,
            notification_adapter=notification_adapter,
            notification_worker_launcher=notification_worker_launcher,
        )
        # Input validation and credential rejection have completed before any state file action.
        state.initialize()
        response = facade.execute(args.command, payload, confirm_live=args.confirm_live)
        exit_code = 0
    except FacadeError as exc:
        response = _error_payload(exc.code, str(exc), exc.next_action)
        exit_code = 2
    except StateConflictError:
        response = _error_payload(
            "STATE_CONFLICT",
            "automated-trading state is unavailable or inconsistent",
            "DISABLE_AUTO_TRADE_AND_INSPECT_STATE",
        )
        exit_code = 2
    except ValueError as exc:
        code = _value_error_code(exc)
        response = _error_payload(code, str(exc), "FIX_REQUEST_OR_USE_MANUAL_CONFIRMATION")
        exit_code = 2
    except (OSError, UnicodeError):
        response = _error_payload("INVALID_REQUEST", "input could not be read", "FIX_REQUEST")
        exit_code = 2
    print(
        json.dumps(
            response,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
