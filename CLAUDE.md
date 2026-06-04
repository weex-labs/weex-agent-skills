# WEEX Agent Repo Guidance

- Treat `skills/` as the only source-of-truth layer.
- Use `skills/weex-trader-skill` for WEEX REST access, profile management, vault operations, and any live order action.
- Use `skills/weex-analysis-skill` for read-only exposure, PnL, fill, and risk review.
- Use `skills/weex-monitor-skill` for WEEX automated monitor requests that create, confirm, evaluate, run, list, or cancel local PnL monitor tasks while delegating live execution to trader.
- Use official WEEX conditional orders through `skills/weex-trader-skill` for price-threshold close requests; do not create local price monitor tasks in `weex-monitor-skill`.
- Never send mutating requests without explicit user confirmation and the live-confirmation flag required by the trader skill.
- Prefer non-argv secret transport when the trader skill offers a safer option.
- When analysis needs live data, collect it first, normalize it into JSON, then pass it into the analysis skill.
