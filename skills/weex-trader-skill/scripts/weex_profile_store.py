#!/usr/bin/env python3
"""Profile metadata and secure credential storage for WEEX scripts."""

from __future__ import annotations

import json
import os
import platform
import secrets
import socket
import subprocess
import tempfile
import time
import uuid
from base64 import b64decode, b64encode
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from hashlib import scrypt as _hashlib_scrypt
except ImportError:  # pragma: no cover
    _hashlib_scrypt = None

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt as _CryptographyScrypt
except ImportError:  # pragma: no cover
    AESGCM = None
    _CryptographyScrypt = None

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from weex_url_policy import BaseUrlPolicyError, validate_weex_base_url


SERVICE_NAME = "weex-trader-skill"
CONFIG_HOME_ENV = "WEEX_TRADER_SKILL_HOME"
METADATA_FILENAME = "profiles.meta.json"
METADATA_LOCK_FILENAME = "profiles.meta.lock"
VAULT_CONFIG_FILENAME = "vault.config.json"
VAULT_DATA_FILENAME = "vault.enc"
VAULT_SESSION_FILENAME = "vault.session.json"
VAULT_LOCK_FILENAME = "vault.lock"
VAULT_AGENT_HOST = "127.0.0.1"
VAULT_AGENT_VERSION = 1
VAULT_AGENT_START_TIMEOUT_SECONDS = 5.0
VAULT_AGENT_CONNECT_TIMEOUT_SECONDS = 1.5
SECURE_FIELDS = ("api_key", "api_secret", "api_passphrase")
FIELD_LABELS = {
    "api_key": "API Key",
    "api_secret": "Secret Key",
    "api_passphrase": "Passphrase",
}
VAULT_VERSION = 1
VAULT_DKLEN = 32
VAULT_SCRYPT_N = 16384
VAULT_SCRYPT_R = 8
VAULT_SCRYPT_P = 1
CRYPTOGRAPHY_RUNTIME_AVAILABLE = AESGCM is not None and _CryptographyScrypt is not None

_MANUAL_SESSION_PROCESSES: Dict[str, subprocess.Popen[str]] = {}


class ProfileError(RuntimeError):
    """Raised when profile metadata or secure storage operations fail."""


def _require_cryptography_runtime() -> None:
    if CRYPTOGRAPHY_RUNTIME_AVAILABLE:
        return
    raise ProfileError(
        "WEEX application vault storage requires Python dependency 'cryptography'. "
        "Run scripts/weex_runtime_setup.py --pretty or install requirements.lock with --require-hashes "
        "using this interpreter and retry."
    )


@dataclass
class ProfileMetadata:
    profile_id: str
    name: str
    description: str = ""
    contract_base_url: str = ""
    spot_base_url: str = ""
    api_key_hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.profile_id,
            "name": self.name,
            "description": self.description,
            "contract_base_url": self.contract_base_url,
            "spot_base_url": self.spot_base_url,
            "api_key_hint": self.api_key_hint,
        }


@dataclass
class ProfileCredentials:
    api_key: str
    api_secret: str
    api_passphrase: str


def config_dir() -> Path:
    raw = os.getenv(CONFIG_HOME_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".weex-trader-skill"


def _has_posix_private_dir_semantics() -> bool:
    return os.name == "posix" and hasattr(os, "getuid")


def _ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass

    if path.is_symlink():
        raise ProfileError(f"Refusing to use symlinked directory for secure storage: {path}")

    stat_result = path.stat()
    if _has_posix_private_dir_semantics():
        if stat_result.st_uid != os.getuid():
            raise ProfileError(f"Secure storage directory is not owned by the current user: {path}")
        if (stat_result.st_mode & 0o077) != 0:
            raise ProfileError(f"Secure storage directory permissions are too broad: {path}")
    return path


def metadata_path() -> Path:
    return config_dir() / METADATA_FILENAME


def metadata_lock_path() -> Path:
    return config_dir() / METADATA_LOCK_FILENAME


def vault_config_path() -> Path:
    return config_dir() / VAULT_CONFIG_FILENAME


def vault_data_path() -> Path:
    return config_dir() / VAULT_DATA_FILENAME


def _require_vault_support() -> None:
    if platform.system() not in {"Windows", "Darwin", "Linux"}:
        raise ProfileError("WEEX application vault is supported on Windows, macOS, and Linux.")


def _require_linux_vault_support() -> None:
    _require_vault_support()


def vault_runtime_dir() -> Path:
    _require_vault_support()
    return _ensure_private_dir(config_dir())


def vault_session_path() -> Path:
    return vault_runtime_dir() / VAULT_SESSION_FILENAME


def vault_lock_path() -> Path:
    return config_dir() / VAULT_LOCK_FILENAME


def _default_vault_config() -> Dict[str, Any]:
    return {
        "version": VAULT_VERSION,
        "backend": "encrypted_file",
        "mode": "manual_once",
        "kdf": {
            "name": "scrypt",
            "n": VAULT_SCRYPT_N,
            "r": VAULT_SCRYPT_R,
            "p": VAULT_SCRYPT_P,
            "dklen": VAULT_DKLEN,
        },
    }


def _load_vault_config() -> Optional[Dict[str, Any]]:
    path = vault_config_path()
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"Invalid vault config JSON at {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProfileError(f"Invalid vault config format at {path}")
    if obj.get("backend") != "encrypted_file":
        raise ProfileError(f"Unsupported vault backend in {path}")
    mode = obj.get("mode")
    if mode != "manual_once":
        raise ProfileError(f"Invalid vault mode in {path}")
    return obj


def _save_vault_config(config: Dict[str, Any]) -> None:
    path = vault_config_path()
    _atomic_write_text(path, json.dumps(config, ensure_ascii=False, indent=2) + "\n", 0o600)


def _active_linux_backend() -> str:
    _require_vault_support()
    config = _load_vault_config()
    if config:
        return "encrypted_file"
    return "encrypted_file_required"


def _require_vault_config() -> Dict[str, Any]:
    config = _load_vault_config()
    if config is None:
        raise ProfileError(
            "WEEX application vault storage requires setup first. "
            "Run scripts/weex_vault.py setup."
        )
    return config


def _empty_vault_payload() -> Dict[str, Any]:
    return {
        "version": VAULT_VERSION,
        "profiles": {},
    }


def _derive_vault_key(passphrase: str, salt: bytes, config: Dict[str, Any]) -> bytes:
    kdf_cfg = config.get("kdf") or {}
    length = int(kdf_cfg.get("dklen", VAULT_DKLEN))
    kwargs = {
        "salt": salt,
        "n": int(kdf_cfg.get("n", VAULT_SCRYPT_N)),
        "r": int(kdf_cfg.get("r", VAULT_SCRYPT_R)),
        "p": int(kdf_cfg.get("p", VAULT_SCRYPT_P)),
        "dklen": length,
    }
    if _hashlib_scrypt is not None:
        return _hashlib_scrypt(passphrase.encode("utf-8"), **kwargs)
    _require_cryptography_runtime()
    assert _CryptographyScrypt is not None
    return _CryptographyScrypt(
        salt=salt,
        length=length,
        n=kwargs["n"],
        r=kwargs["r"],
        p=kwargs["p"],
    ).derive(passphrase.encode("utf-8"))


def _load_vault_file() -> Dict[str, Any]:
    path = vault_data_path()
    if not path.exists():
        raise ProfileError(f"Vault data file was not found at {path}. Run scripts/weex_vault.py setup.")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"Invalid vault data JSON at {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProfileError(f"Invalid vault data format at {path}")
    return obj


def _save_vault_file(obj: Dict[str, Any]) -> None:
    path = vault_data_path()
    _atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n", 0o600)


@contextmanager
def _file_lock(lock_path: Path) -> Any:
    _ensure_private_dir(lock_path.parent)
    with open(lock_path, "a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _vault_lock() -> Any:
    with _file_lock(vault_lock_path()):
        yield


@contextmanager
def _metadata_lock() -> Any:
    with _file_lock(metadata_lock_path()):
        yield


def _atomic_write_text(path: Path, text: str, mode: int) -> None:
    _ensure_private_dir(path.parent)
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _encrypt_vault_payload(payload: Dict[str, Any], key: bytes, config: Dict[str, Any], salt: bytes) -> Dict[str, Any]:
    _require_cryptography_runtime()
    nonce = os.urandom(12)
    plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    assert AESGCM is not None
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return {
        "version": VAULT_VERSION,
        "kdf": {
            "name": "scrypt",
            "salt": b64encode(salt).decode("ascii"),
            "n": int(config.get("kdf", {}).get("n", VAULT_SCRYPT_N)),
            "r": int(config.get("kdf", {}).get("r", VAULT_SCRYPT_R)),
            "p": int(config.get("kdf", {}).get("p", VAULT_SCRYPT_P)),
            "dklen": int(config.get("kdf", {}).get("dklen", VAULT_DKLEN)),
        },
        "cipher": {
            "name": "aes-256-gcm",
            "nonce": b64encode(nonce).decode("ascii"),
        },
        "ciphertext": b64encode(ciphertext).decode("ascii"),
    }


def _decrypt_vault_payload(raw: Dict[str, Any], key: bytes) -> Dict[str, Any]:
    _require_cryptography_runtime()
    try:
        nonce = b64decode(raw["cipher"]["nonce"])
        ciphertext = b64decode(raw["ciphertext"])
        assert AESGCM is not None
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        payload = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise ProfileError("Unable to decrypt vault data. Check the vault password or vault configuration.") from exc
    if not isinstance(payload, dict):
        raise ProfileError("Invalid decrypted vault payload")
    if not isinstance(payload.get("profiles", {}), dict):
        raise ProfileError("Invalid decrypted vault profiles map")
    return payload


def _session_key_payload(key: bytes) -> Dict[str, Any]:
    return {
        "version": VAULT_AGENT_VERSION,
        "host": VAULT_AGENT_HOST,
        "key": b64encode(key).decode("ascii"),
    }


def _remove_vault_session_descriptor() -> None:
    path = vault_session_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _read_vault_session_descriptor() -> Optional[Dict[str, Any]]:
    path = vault_session_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        host = raw.get("host")
        port = raw.get("port")
        token = raw.get("token")
        pid = raw.get("pid")
        if host != VAULT_AGENT_HOST:
            return None
        if not isinstance(port, int) or port <= 0:
            return None
        if not isinstance(token, str) or not token:
            return None
        if not isinstance(pid, int) or pid <= 0:
            return None
        return {
            "version": int(raw.get("version", 0)),
            "host": host,
            "port": port,
            "token": token,
            "pid": pid,
        }
    except Exception:
        return None


def _request_vault_session(
    action: str,
    *,
    cleanup_on_failure: bool = True,
    session: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    descriptor = session or _read_vault_session_descriptor()
    if descriptor is None:
        return None
    try:
        with socket.create_connection(
            (str(descriptor["host"]), int(descriptor["port"])),
            timeout=VAULT_AGENT_CONNECT_TIMEOUT_SECONDS,
        ) as sock:
            sock.settimeout(VAULT_AGENT_CONNECT_TIMEOUT_SECONDS)
            request = {
                "action": action,
                "token": descriptor["token"],
            }
            sock.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            chunks: List[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError:
        if cleanup_on_failure:
            _remove_vault_session_descriptor()
        return None

    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except Exception:
        if cleanup_on_failure:
            _remove_vault_session_descriptor()
        return None
    if not isinstance(payload, dict):
        if cleanup_on_failure:
            _remove_vault_session_descriptor()
        return None
    if payload.get("ok") is not True:
        if cleanup_on_failure:
            _remove_vault_session_descriptor()
        return None
    return payload


def _write_vault_session_key(key: bytes) -> None:
    # Replace the previous manual_once agent before publishing a new session.
    _delete_vault_session_key()
    script_path = Path(__file__).with_name("weex_vault_agent.py")
    if not script_path.is_file():
        raise ProfileError(f"Vault session agent was not found at {script_path}.")

    popen_kwargs: Dict[str, Any] = {
        "cwd": str(script_path.parent),
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        creationflags = 0
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(
        [os.sys.executable, str(script_path), "--session-file", str(vault_session_path())],
        **popen_kwargs,
    )
    bootstrap = json.dumps({"key": b64encode(key).decode("ascii")}, ensure_ascii=False) + "\n"
    assert process.stdin is not None
    process.stdin.write(bootstrap)
    process.stdin.flush()
    process.stdin.close()

    deadline = time.time() + VAULT_AGENT_START_TIMEOUT_SECONDS
    while time.time() < deadline:
        response = _request_vault_session("ping")
        if response is not None:
            _MANUAL_SESSION_PROCESSES[str(vault_session_path())] = process
            return
        if process.poll() is not None:
            stderr = process.stderr.read().strip() if process.stderr is not None else ""
            raise ProfileError(
                "Unable to start the WEEX vault session agent."
                + (f" {stderr}" if stderr else "")
            )
        time.sleep(0.1)

    try:
        process.kill()
    except OSError:
        pass
    _remove_vault_session_descriptor()
    raise ProfileError("Timed out while starting the WEEX vault session agent.")


def _read_vault_session_key() -> Optional[bytes]:
    try:
        raw = _request_vault_session("get_key")
        if raw is None:
            return None
        key_raw = raw.get("key")
        if not isinstance(key_raw, str) or not key_raw:
            return None
        return b64decode(key_raw)
    except Exception:
        return None


def _delete_vault_session_key() -> None:
    _request_vault_session("shutdown", cleanup_on_failure=False)
    process = _MANUAL_SESSION_PROCESSES.pop(str(vault_session_path()), None)
    if process is not None:
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
    _remove_vault_session_descriptor()


def setup_linux_vault(
    mode: str,
    passphrase: str,
    unlock: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    _require_vault_support()
    normalized_mode = (mode or "").strip()
    if normalized_mode != "manual_once":
        raise ProfileError("Vault mode must be manual_once.")
    cleaned_passphrase = _clean_text(passphrase)
    if not cleaned_passphrase:
        raise ProfileError("Vault passphrase cannot be empty.")
    vault_session_path()
    config = _default_vault_config()
    if not force and (vault_config_path().exists() or vault_data_path().exists()):
        raise ProfileError(f"Vault already exists at {vault_data_path()}. Use --force to overwrite it.")
    salt = os.urandom(16)
    key = _derive_vault_key(cleaned_passphrase, salt, config)
    _save_vault_config(config)
    _save_vault_file(_encrypt_vault_payload(_empty_vault_payload(), key, config, salt))
    if force:
        _save_metadata_store(_default_store())
    if unlock:
        _write_vault_session_key(key)
    else:
        _delete_vault_session_key()
    return vault_status()


def _resolve_vault_key() -> bytes:
    config = _require_vault_config()
    raw_vault = _load_vault_file()
    mode = config.get("mode")
    if mode == "manual_once":
        key = _read_vault_session_key()
        if key is None:
            raise ProfileError(
                "Vault is locked. Run scripts/weex_vault.py unlock before using private WEEX profiles."
            )
        _decrypt_vault_payload(raw_vault, key)
        return key
    raise ProfileError("Unsupported vault mode.")


def unlock_linux_vault(passphrase: str) -> Dict[str, Any]:
    config = _require_vault_config()
    if config.get("mode") != "manual_once":
        raise ProfileError("Unlock is only required for vault mode manual_once.")
    cleaned_passphrase = _clean_text(passphrase)
    if not cleaned_passphrase:
        raise ProfileError("Vault passphrase cannot be empty.")
    vault_session_path()
    raw_vault = _load_vault_file()
    salt = b64decode(raw_vault["kdf"]["salt"])
    key = _derive_vault_key(cleaned_passphrase, salt, config)
    _decrypt_vault_payload(raw_vault, key)
    _write_vault_session_key(key)
    return vault_status()


def change_linux_vault_password(
    current_passphrase: str,
    new_passphrase: str,
) -> Dict[str, Any]:
    config = _require_vault_config()
    cleaned_current = _clean_text(current_passphrase)
    cleaned_new = _clean_text(new_passphrase)
    if not cleaned_current:
        raise ProfileError("Current vault passphrase cannot be empty.")
    if not cleaned_new:
        raise ProfileError("New vault passphrase cannot be empty.")

    with _vault_lock():
        raw_vault = _load_vault_file()
        salt = b64decode(raw_vault["kdf"]["salt"])
        current_key = _derive_vault_key(cleaned_current, salt, config)
        payload = _decrypt_vault_payload(raw_vault, current_key)

        session_was_unlocked = False
        if config.get("mode") == "manual_once":
            session_key = _read_vault_session_key()
            if session_key is not None:
                try:
                    _decrypt_vault_payload(raw_vault, session_key)
                except ProfileError:
                    session_was_unlocked = False
                else:
                    session_was_unlocked = True

        new_salt = os.urandom(16)
        new_key = _derive_vault_key(cleaned_new, new_salt, config)
        _save_vault_file(_encrypt_vault_payload(payload, new_key, config, new_salt))

        if session_was_unlocked:
            _write_vault_session_key(new_key)
        else:
            _delete_vault_session_key()

    return vault_status()


def lock_linux_vault() -> Dict[str, Any]:
    _delete_vault_session_key()
    return vault_status()


def reset_linux_vault() -> Dict[str, Any]:
    _require_vault_support()
    with _vault_lock():
        with _metadata_lock():
            _delete_vault_session_key()
            for path in (vault_config_path(), vault_data_path()):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            _save_metadata_store(_default_store())
    return vault_status()


def vault_status() -> Dict[str, Any]:
    _require_vault_support()
    config = _load_vault_config()
    configured = config is not None
    mode = config.get("mode") if config else None
    state = "uninitialized"
    backend = "Application Vault (setup required)"
    if configured:
        backend = f"Application Vault ({mode})"
        try:
            _resolve_vault_key()
            state = "unlocked"
            action_required = None
        except ProfileError as exc:
            message = str(exc).lower()
            if "locked" in message:
                state = "locked"
                action_required = "unlock"
            else:
                state = "misconfigured"
                action_required = "repair"
    else:
        action_required = "setup"
    try:
        session_path = vault_session_path()
    except ProfileError:
        session_path = None
    return {
        "ok": True,
        "backend": backend,
        "configured": configured,
        "mode": mode,
        "state": state,
        "action_required": action_required,
        "vault_config_path": str(vault_config_path()),
        "vault_data_path": str(vault_data_path()),
        "vault_session_path": str(session_path) if session_path is not None else None,
    }


def _load_vault_profiles() -> Dict[str, Dict[str, str]]:
    key = _resolve_vault_key()
    payload = _decrypt_vault_payload(_load_vault_file(), key)
    profiles = payload.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ProfileError("Invalid vault profile map")
    normalized: Dict[str, Dict[str, str]] = {}
    for profile_id, fields in profiles.items():
        if not isinstance(profile_id, str) or not profile_id.strip():
            continue
        if not isinstance(fields, dict):
            continue
        clean_fields: Dict[str, str] = {}
        for field_name in SECURE_FIELDS:
            value = fields.get(field_name)
            if isinstance(value, str) and value:
                clean_fields[field_name] = value
        if clean_fields:
            normalized[profile_id] = clean_fields
    return normalized


def _save_vault_profiles(profiles: Dict[str, Dict[str, str]]) -> None:
    with _vault_lock():
        key = _resolve_vault_key()
        config = _require_vault_config()
        raw_vault = _load_vault_file()
        salt = b64decode(raw_vault["kdf"]["salt"])
        payload = {
            "version": VAULT_VERSION,
            "profiles": profiles,
        }
        _save_vault_file(_encrypt_vault_payload(payload, key, config, salt))


def _update_vault_profile_credentials(profile_name: str, credentials: ProfileCredentials) -> None:
    with _vault_lock():
        key = _resolve_vault_key()
        config = _require_vault_config()
        raw_vault = _load_vault_file()
        salt = b64decode(raw_vault["kdf"]["salt"])
        payload = _decrypt_vault_payload(raw_vault, key)
        profiles = payload.setdefault("profiles", {})
        if not isinstance(profiles, dict):
            raise ProfileError("Invalid vault profile map")
        profiles[profile_name] = {
            "api_key": credentials.api_key,
            "api_secret": credentials.api_secret,
            "api_passphrase": credentials.api_passphrase,
        }
        _save_vault_file(_encrypt_vault_payload(payload, key, config, salt))


def _remove_vault_profile_credentials(profile_name: str) -> bool:
    with _vault_lock():
        key = _resolve_vault_key()
        config = _require_vault_config()
        raw_vault = _load_vault_file()
        salt = b64decode(raw_vault["kdf"]["salt"])
        payload = _decrypt_vault_payload(raw_vault, key)
        profiles = payload.get("profiles", {})
        if not isinstance(profiles, dict):
            raise ProfileError("Invalid vault profile map")
        if profile_name not in profiles:
            return False
        del profiles[profile_name]
        _save_vault_file(_encrypt_vault_payload(payload, key, config, salt))
        return True


def secure_store_backend_name() -> str:
    _require_vault_support()
    backend = _active_linux_backend()
    if backend == "encrypted_file":
        mode = _require_vault_config().get("mode")
        return f"Application Vault ({mode})"
    return "Application Vault (setup required)"


def _default_store() -> Dict[str, Any]:
    return {
        "version": 2,
        "default_profile_id": None,
        "profiles": {},
    }


def _clean_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _clean_optional_base_url(value: Optional[str], field_name: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    try:
        return validate_weex_base_url(cleaned, label=field_name)
    except BaseUrlPolicyError as exc:
        raise ProfileError(str(exc)) from exc


def _profile_from_raw(name: str, raw: Any) -> ProfileMetadata:
    if not isinstance(raw, dict):
        raise ProfileError(f"Invalid profile metadata for '{name}'")
    profile_id = _clean_text(raw.get("id")) or _clean_text(name)
    profile_name = _clean_text(raw.get("name")) or _clean_text(name)
    if not profile_id:
        raise ProfileError(f"Invalid profile id for '{name}'")
    if not profile_name:
        raise ProfileError(f"Invalid profile name for '{name}'")
    return ProfileMetadata(
        profile_id=profile_id,
        name=profile_name,
        description=_clean_text(raw.get("description")),
        contract_base_url=_clean_text(raw.get("contract_base_url")),
        spot_base_url=_clean_text(raw.get("spot_base_url")),
        api_key_hint=_clean_text(raw.get("api_key_hint")),
    )


def _resolve_default_profile_id(obj: Dict[str, Any], profiles: Dict[str, Dict[str, Any]]) -> Optional[str]:
    default_profile_id = obj.get("default_profile_id")
    if isinstance(default_profile_id, str) and default_profile_id.strip():
        key = default_profile_id.strip()
        if key in profiles:
            return key

    legacy_default = obj.get("default_profile")
    if isinstance(legacy_default, str) and legacy_default.strip():
        legacy_key = legacy_default.strip()
        if legacy_key in profiles:
            return legacy_key
        for profile_id, raw in profiles.items():
            if _clean_text(raw.get("name")) == legacy_key:
                return profile_id
    return None


def load_metadata_store() -> Dict[str, Any]:
    path = metadata_path()
    if not path.exists():
        return _default_store()
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"Invalid profile metadata JSON at {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProfileError(f"Invalid profile metadata format at {path}")
    store = _default_store()
    store["version"] = max(int(obj.get("version", 1)), 1)
    raw_profiles = obj.get("profiles", {})
    if not isinstance(raw_profiles, dict):
        raise ProfileError(f"Invalid profiles map at {path}")
    profiles: Dict[str, Dict[str, Any]] = {}
    seen_names = set()
    for name, raw in raw_profiles.items():
        if not isinstance(name, str) or not name.strip():
            raise ProfileError(f"Invalid profile name in {path}")
        profile = _profile_from_raw(name.strip(), raw)
        if profile.profile_id in profiles:
            raise ProfileError(f"Duplicate profile id '{profile.profile_id}' in {path}")
        if profile.name in seen_names:
            raise ProfileError(f"Duplicate profile name '{profile.name}' in {path}")
        profiles[profile.profile_id] = profile.to_dict()
        seen_names.add(profile.name)
    store["profiles"] = profiles
    store["default_profile_id"] = _resolve_default_profile_id(obj, profiles)
    return store


def _save_metadata_store(store: Dict[str, Any]) -> None:
    path = metadata_path()
    _atomic_write_text(path, json.dumps(store, ensure_ascii=False, indent=2) + "\n", 0o600)


def _resolve_profile_from_store(store: Dict[str, Any], name: Optional[str]) -> Optional[ProfileMetadata]:
    requested = _clean_text(name)
    if requested:
        raw = store["profiles"].get(requested)
        if raw is not None:
            return _profile_from_raw(requested, raw)
        for profile_id, profile_raw in store["profiles"].items():
            profile = _profile_from_raw(profile_id, profile_raw)
            if profile.name == requested:
                return profile
        raise ProfileError(
            f"Profile '{requested}' was not found in {metadata_path()}. "
            "Open the profile manager or update the metadata file."
        )

    selected = store.get("default_profile_id")
    if not isinstance(selected, str) or not selected.strip():
        return None
    raw = store["profiles"].get(selected)
    if raw is None:
        raise ProfileError(
            f"Profile '{selected}' was not found in {metadata_path()}. "
            "Open the profile manager or update the metadata file."
        )
    return _profile_from_raw(selected, raw)


def list_profiles() -> List[ProfileMetadata]:
    store = load_metadata_store()
    profiles = []
    for profile_id, raw in store["profiles"].items():
        profiles.append(_profile_from_raw(profile_id, raw))
    return sorted(profiles, key=lambda item: item.name.lower())


def get_profile_by_id(profile_id: str) -> Optional[ProfileMetadata]:
    key = _clean_text(profile_id)
    if not key:
        return None
    store = load_metadata_store()
    raw = store["profiles"].get(key)
    if raw is None:
        return None
    return _profile_from_raw(key, raw)


def get_profile(name: str) -> Optional[ProfileMetadata]:
    key = _clean_text(name)
    if not key:
        return None
    store = load_metadata_store()
    raw = store["profiles"].get(key)
    if raw is not None:
        return _profile_from_raw(key, raw)
    for profile_id, profile_raw in store["profiles"].items():
        profile = _profile_from_raw(profile_id, profile_raw)
        if profile.name == key:
            return profile
    return None


def get_default_profile_id() -> Optional[str]:
    store = load_metadata_store()
    profile_id = store.get("default_profile_id")
    if not isinstance(profile_id, str) or not profile_id.strip():
        return None
    key = profile_id.strip()
    if key not in store["profiles"]:
        return None
    return key


def get_default_profile_name() -> Optional[str]:
    profile_id = get_default_profile_id()
    if profile_id is None:
        return None
    profile = get_profile_by_id(profile_id)
    return profile.name if profile is not None else None


def resolve_profile(name: Optional[str]) -> Optional[ProfileMetadata]:
    store = load_metadata_store()
    return _resolve_profile_from_store(store, name)


def make_api_key_hint(api_key: str) -> str:
    cleaned = _clean_text(api_key)
    if not cleaned:
        return ""
    tail = cleaned[-4:] if len(cleaned) >= 4 else cleaned
    return f"***{tail}"


def save_profile_metadata(profile: ProfileMetadata, set_default: Optional[bool] = None) -> None:
    with _metadata_lock():
        store = load_metadata_store()
        store["version"] = 2
        store["profiles"][profile.profile_id] = profile.to_dict()
        if set_default is True:
            store["default_profile_id"] = profile.profile_id
        _save_metadata_store(store)


def set_default_profile(name: Optional[str]) -> None:
    with _metadata_lock():
        store = load_metadata_store()
        if name is None:
            store["default_profile_id"] = None
            _save_metadata_store(store)
            return
        profile = _resolve_profile_from_store(store, name)
        if profile is None:
            raise ProfileError(f"Profile '{_clean_text(name)}' was not found in {metadata_path()}")
        store["default_profile_id"] = profile.profile_id
        _save_metadata_store(store)


def delete_profile_metadata_by_id(profile_id: str) -> bool:
    key = _clean_text(profile_id)
    with _metadata_lock():
        store = load_metadata_store()
        if key not in store["profiles"]:
            return False
        del store["profiles"][key]
        if store.get("default_profile_id") == key:
            store["default_profile_id"] = None
        _save_metadata_store(store)
        return True


def delete_profile_metadata(name: str) -> bool:
    profile = get_profile(name)
    if profile is None:
        return False
    return delete_profile_metadata_by_id(profile.profile_id)


def upsert_profile(
    name: str,
    description: Optional[str] = None,
    contract_base_url: Optional[str] = None,
    spot_base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    api_passphrase: Optional[str] = None,
    profile_id: Optional[str] = None,
    set_default: Optional[bool] = None,
) -> ProfileMetadata:
    key = _clean_text(name)
    if not key:
        raise ProfileError("profile name cannot be empty")

    current_profile_id = _clean_text(profile_id)
    if current_profile_id:
        existing = get_profile_by_id(current_profile_id)
        if existing is None:
            raise ProfileError(f"Profile id '{current_profile_id}' was not found in {metadata_path()}")
    else:
        existing = get_profile(key)

    duplicate = get_profile(key)
    if duplicate is not None and (existing is None or duplicate.profile_id != existing.profile_id):
        raise ProfileError(f"profile name '{key}' already exists")

    effective_contract_base_url = (
        _clean_optional_base_url(contract_base_url, "contract_base_url")
        if contract_base_url is not None
        else (existing.contract_base_url if existing else "")
    )
    effective_spot_base_url = (
        _clean_optional_base_url(spot_base_url, "spot_base_url")
        if spot_base_url is not None
        else (existing.spot_base_url if existing else "")
    )

    secret_values = [api_key, api_secret, api_passphrase]
    has_secret_update = any(value is not None for value in secret_values)
    effective_profile_id = existing.profile_id if existing is not None else uuid.uuid4().hex

    if has_secret_update:
        cleaned_key = _clean_text(api_key)
        cleaned_secret = _clean_text(api_secret)
        cleaned_passphrase = _clean_text(api_passphrase)
        if not cleaned_key or not cleaned_secret or not cleaned_passphrase:
            raise ProfileError("api_key, api_secret, and api_passphrase must all be provided together")
        save_profile_credentials(
            effective_profile_id,
            ProfileCredentials(
                api_key=cleaned_key,
                api_secret=cleaned_secret,
                api_passphrase=cleaned_passphrase,
            ),
        )
        if not _profile_id_has_credentials(effective_profile_id):
            raise ProfileError(
                "Secure credentials could not be verified after saving. "
                "Check the active credential backend and try again."
            )
        api_key_hint = make_api_key_hint(cleaned_key)
    elif existing is None:
        raise ProfileError("new profiles require api_key, api_secret, and api_passphrase")
    elif not _profile_id_has_credentials(existing.profile_id):
        raise ProfileError(
            "This profile does not currently have complete secure credentials. "
            "Re-enter api_key, api_secret, and api_passphrase before saving."
        )
    else:
        api_key_hint = existing.api_key_hint

    profile = ProfileMetadata(
        profile_id=effective_profile_id,
        name=key,
        description=_clean_text(description) if description is not None else (existing.description if existing else ""),
        contract_base_url=effective_contract_base_url,
        spot_base_url=effective_spot_base_url,
        api_key_hint=api_key_hint,
    )
    save_profile_metadata(profile, set_default=set_default)
    return profile


def profile_has_credentials(name: str) -> bool:
    profile = get_profile(name)
    storage_key = profile.profile_id if profile is not None else _clean_text(name)
    return _profile_id_has_credentials(storage_key)


def _profile_id_has_credentials(profile_id: str) -> bool:
    key = _clean_text(profile_id)
    return bool(key) and all(_load_secret(key, field_name) is not None for field_name in SECURE_FIELDS)


def profile_has_credentials_by_id(profile_id: str) -> bool:
    return _profile_id_has_credentials(profile_id)


def load_profile_credentials(name: str) -> ProfileCredentials:
    key = _clean_text(name)
    if not key:
        raise ProfileError("profile name cannot be empty")
    profile = resolve_profile(key)
    storage_key = profile.profile_id if profile is not None else key
    profile_label = profile.name if profile is not None else key
    values: Dict[str, str] = {}
    missing: List[str] = []
    for field_name in SECURE_FIELDS:
        value = _load_secret(storage_key, field_name)
        if value is None:
            missing.append(FIELD_LABELS[field_name])
            continue
        values[field_name] = value
    if missing:
        missing_str = ", ".join(missing)
        raise ProfileError(
            f"Profile '{profile_label}' is missing secure credentials: {missing_str}. "
            "Open the profile manager or update the profile with scripts/weex_profiles.py."
        )
    return ProfileCredentials(
        api_key=values["api_key"],
        api_secret=values["api_secret"],
        api_passphrase=values["api_passphrase"],
    )


def save_profile_credentials(name: str, credentials: ProfileCredentials) -> None:
    key = _clean_text(name)
    if not key:
        raise ProfileError("profile name cannot be empty")
    backend = _active_linux_backend()
    if backend == "encrypted_file":
        # Update the full credential tuple in one vault write so interruptions
        # cannot leave a partially populated profile behind.
        _update_vault_profile_credentials(key, credentials)
        return
    if backend == "encrypted_file_required":
        raise ProfileError(
            "WEEX application vault storage is not initialized yet. "
            "Run scripts/weex_vault.py setup before saving WEEX credentials."
        )
    raise ProfileError("Unexpected secure store backend state.")


def delete_profile_credentials(name: str) -> bool:
    profile = get_profile(name)
    key = profile.profile_id if profile is not None else _clean_text(name)
    if not key:
        return False
    backend = _active_linux_backend()
    if backend == "encrypted_file":
        return _remove_vault_profile_credentials(key)
    if backend == "encrypted_file_required":
        return False
    raise ProfileError("Unexpected secure store backend state.")


def delete_profile_by_id(profile_id: str) -> bool:
    key = _clean_text(profile_id)
    if not key:
        return False

    # Delete credentials first so a locked or unavailable secure backend does not
    # leave metadata cleared while secrets still remain in storage.
    credentials_deleted = delete_profile_credentials(key)
    metadata_deleted = delete_profile_metadata_by_id(key)
    return metadata_deleted or credentials_deleted


def delete_profile(name: str) -> bool:
    try:
        profile = resolve_profile(name)
    except ProfileError:
        profile = None
    if profile is None:
        key = _clean_text(name)
        metadata_deleted = delete_profile_metadata_by_id(key)
        credentials_deleted = delete_profile_credentials(key)
        return metadata_deleted or credentials_deleted
    return delete_profile_by_id(profile.profile_id)


def _store_secret(profile_name: str, field_name: str, value: str) -> None:
    _store_secret_linux(profile_name, field_name, value)


def _load_secret(profile_name: str, field_name: str) -> Optional[str]:
    return _load_secret_linux(profile_name, field_name)


def _delete_secret(profile_name: str, field_name: str) -> bool:
    return _delete_secret_linux(profile_name, field_name)


def _store_secret_linux(profile_name: str, field_name: str, value: str) -> None:
    backend = _active_linux_backend()
    if backend == "encrypted_file":
        profiles = _load_vault_profiles()
        entry = profiles.setdefault(profile_name, {})
        entry[field_name] = value
        _save_vault_profiles(profiles)
        return
    if backend == "encrypted_file_required":
        raise ProfileError(
            "WEEX application vault storage is not initialized yet. "
            "Run scripts/weex_vault.py setup before saving WEEX credentials."
        )
    raise ProfileError("Unexpected Linux backend state.")


def _load_secret_linux(profile_name: str, field_name: str) -> Optional[str]:
    backend = _active_linux_backend()
    if backend == "encrypted_file":
        profiles = _load_vault_profiles()
        value = profiles.get(profile_name, {}).get(field_name)
        return value if isinstance(value, str) and value else None
    if backend == "encrypted_file_required":
        raise ProfileError(
            "WEEX application vault storage is not initialized yet. "
            "Run scripts/weex_vault.py setup before loading WEEX credentials."
        )
    raise ProfileError("Unexpected Linux backend state.")


def _delete_secret_linux(profile_name: str, field_name: str) -> bool:
    backend = _active_linux_backend()
    if backend == "encrypted_file":
        profiles = _load_vault_profiles()
        entry = profiles.get(profile_name)
        if not entry or field_name not in entry:
            return False
        del entry[field_name]
        if not entry:
            del profiles[profile_name]
        _save_vault_profiles(profiles)
        return True
    if backend == "encrypted_file_required":
        return False
    raise ProfileError("Unexpected Linux backend state.")
