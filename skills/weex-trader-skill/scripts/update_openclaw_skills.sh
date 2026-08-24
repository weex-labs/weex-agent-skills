#!/usr/bin/env bash
set -euo pipefail

readonly DEFAULT_REPO_URL="https://github.com/weex-labs/weex-agent-skills"
readonly DEFAULT_BRANCH="main"
readonly APPROVED_COMMIT="e10c2089550159afb7247271d1041d9b415145cd"
readonly OPENCLAW_ROOT="${OPENCLAW_HOME:-${HOME}/.openclaw}"
readonly REPO_DIR="${WEEX_OPENCLAW_REPO_DIR:-${OPENCLAW_ROOT}/skill-repos/weex-agent-skills}"
readonly SKILLS_DIR="${WEEX_OPENCLAW_SKILLS_DIR:-${OPENCLAW_ROOT}/skills}"
readonly BIN_LINK="${WEEX_OPENCLAW_BIN_LINK:-${HOME}/bin/update-weex-openclaw-skills.sh}"
readonly SCRIPT_RELATIVE_PATH="skills/weex-trader-skill/scripts/update_openclaw_skills.sh"
readonly STABLE_UPDATER="${OPENCLAW_ROOT}/update-weex-openclaw-skills.sh"

readonly -a WEEX_SKILLS=(
  "weex-trader-skill"
  "weex-analysis-skill"
  "weex-monitor-skill"
  "weex-partner-skill"
)

DEV_MODE=0
for argument in "$@"; do
  case "${argument}" in
    --dev) DEV_MODE=1 ;;
    --help|-h)
      printf 'Usage: %s [--dev]\n' "$0"
      printf '%s\n' 'Production mode uses the pinned official release commit.'
      printf '%s\n' '--dev permits explicit local/non-official repository and branch overrides.'
      exit 0
      ;;
    *) printf 'Error: unknown argument: %s\n' "${argument}" >&2; exit 1 ;;
  esac
done

if (( DEV_MODE == 0 )); then
  if [[ -n "${WEEX_OPENCLAW_REPO_URL+x}" || -n "${WEEX_OPENCLAW_BRANCH+x}" ]]; then
    printf 'Error: repository and branch overrides require explicit --dev mode\n' >&2
    exit 1
  fi
  REPO_URL="${DEFAULT_REPO_URL}"
  BRANCH="${DEFAULT_BRANCH}"
else
  REPO_URL="${WEEX_OPENCLAW_REPO_URL:-${DEFAULT_REPO_URL}}"
  BRANCH="${WEEX_OPENCLAW_BRANCH:-${DEFAULT_BRANCH}}"
fi

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

canonical_repo_url() {
  local value="$1"
  value="${value%/}"
  value="${value%.git}"
  printf '%s' "${value}"
}

validate_source() {
  if (( DEV_MODE == 1 )); then
    [[ -n "${REPO_URL}" && -n "${BRANCH}" ]] || fail "development repository and branch must be non-empty"
    return
  fi
  [[ "$(canonical_repo_url "${REPO_URL}")" == "$(canonical_repo_url "${DEFAULT_REPO_URL}")" ]] \
    || fail "production updates require the official repository"
  [[ "${BRANCH}" == "${DEFAULT_BRANCH}" ]] || fail "production updates require the main branch"
}

validate_checkout() {
  local checkout="$1"
  local commit

  commit="$(git -C "${checkout}" rev-parse --verify 'HEAD^{commit}')" \
    || fail "unable to resolve checkout commit"
  if (( DEV_MODE == 0 )) && [[ "${commit}" != "${APPROVED_COMMIT}" ]]; then
    fail "checkout commit ${commit} is not the approved release ${APPROVED_COMMIT}"
  fi
  git -C "${checkout}" diff-index --quiet HEAD -- \
    || fail "checkout contains unexpected working-tree changes"
  git -C "${checkout}" fsck --strict --no-progress >/dev/null \
    || fail "checkout integrity verification failed"

  local skill_name skill_path
  for skill_name in "${WEEX_SKILLS[@]}"; do
    skill_path="${checkout}/skills/${skill_name}"
    [[ -d "${skill_path}" && ! -L "${skill_path}" ]] \
      || fail "invalid skill checkout: ${skill_name}"
    [[ -f "${skill_path}/SKILL.md" && ! -L "${skill_path}/SKILL.md" ]] \
      || fail "invalid skill checkout; missing regular SKILL.md: ${skill_name}"
  done
  if git -C "${checkout}" ls-files -s -- \
      skills/weex-trader-skill skills/weex-analysis-skill skills/weex-monitor-skill skills/weex-partner-skill \
      | awk '$1 ~ /^160000$/ { found=1 } END { exit found ? 0 : 1 }'; then
    fail "submodules are not permitted in the OpenClaw skill checkout"
  fi
}

ensure_symlink() {
  local source_path="$1"
  local destination_path="$2"
  local temporary_path="${destination_path}.tmp.$$"

  [[ -e "${source_path}" ]] || fail "symlink source does not exist: ${source_path}"
  mkdir -p "$(dirname "${destination_path}")"

  if [[ -e "${destination_path}" || -L "${destination_path}" ]] && [[ ! -L "${destination_path}" ]]; then
    fail "destination exists and is not a symbolic link: ${destination_path}"
  fi
  rm -f "${temporary_path}"
  ln -s "${source_path}" "${temporary_path}"
  # BSD/GNU `mv` may treat a symlink-to-directory destination as a directory
  # and move the temporary link inside it. Remove only the existing symlink so
  # the destination cannot silently keep pointing at the previous checkout.
  if [[ -L "${destination_path}" ]]; then
    rm -f "${destination_path}"
  fi
  mv -f "${temporary_path}" "${destination_path}"
}

declare -a LINK_PATHS=()
declare -a LINK_OLD_TARGETS=()
declare -a LINK_OLD_PRESENT=()

record_link_state() {
  local path="$1"
  LINK_PATHS+=("${path}")
  if [[ -L "${path}" ]]; then
    LINK_OLD_TARGETS+=("$(readlink "${path}")")
    LINK_OLD_PRESENT+=(1)
  elif [[ -e "${path}" ]]; then
    fail "destination exists and is not a symbolic link: ${path}"
  else
    LINK_OLD_TARGETS+=("")
    LINK_OLD_PRESENT+=(0)
  fi
}

record_all_link_states() {
  local skill_name
  mkdir -p "${SKILLS_DIR}"
  for skill_name in "${WEEX_SKILLS[@]}"; do
    record_link_state "${SKILLS_DIR}/${skill_name}"
  done
  record_link_state "${BIN_LINK}"
}

restore_links() {
  local index path
  for index in "${!LINK_PATHS[@]}"; do
    path="${LINK_PATHS[${index}]}"
    if [[ -L "${path}" ]]; then
      rm -f "${path}"
    fi
    if [[ "${LINK_OLD_PRESENT[${index}]}" == 1 ]]; then
      mkdir -p "$(dirname "${path}")"
      ln -s "${LINK_OLD_TARGETS[${index}]}" "${path}"
    fi
  done
}

TMP_CHECKOUT=""
TMP_UPDATER_COPY=""
OLD_REPO_BACKUP=""
OLD_UPDATER_BACKUP=""
UPDATE_SUCCEEDED=0

update_repository() {
  TMP_CHECKOUT="$(mktemp -d "$(dirname "${REPO_DIR}")/.weex-openclaw-update.XXXXXX")"
  git clone --quiet --no-tags --single-branch --branch "${BRANCH}" "${REPO_URL}" "${TMP_CHECKOUT}"
  validate_checkout "${TMP_CHECKOUT}"

  if [[ -e "${REPO_DIR}" || -L "${REPO_DIR}" ]]; then
    [[ ! -L "${REPO_DIR}" && -d "${REPO_DIR}/.git" ]] \
      || fail "repository path is not a Git checkout: ${REPO_DIR}"
    OLD_REPO_BACKUP="${REPO_DIR}.previous.$$"
    [[ ! -e "${OLD_REPO_BACKUP}" ]] || fail "repository backup path already exists: ${OLD_REPO_BACKUP}"
    mv "${REPO_DIR}" "${OLD_REPO_BACKUP}"
  fi
  mv "${TMP_CHECKOUT}" "${REPO_DIR}"
  TMP_CHECKOUT=""
}

link_skills() {
  local skill_name

  for skill_name in "${WEEX_SKILLS[@]}"; do
    ensure_symlink "${REPO_DIR}/skills/${skill_name}" "${SKILLS_DIR}/${skill_name}"
  done
}

validate_openclaw() {
  require_command openclaw
  openclaw skills list --eligible
  openclaw skills info weex-trader-skill
  openclaw skills check
}

install_stable_updater() {
  # Keep the bootstrap that is already executing as the updater trust root;
  # never replace it with code from the newly fetched skill checkout.
  local updater_source="${BASH_SOURCE[0]}"
  [[ -f "${updater_source}" ]] \
    || fail "trusted updater source is unavailable"
  TMP_UPDATER_COPY="${STABLE_UPDATER}.tmp.$$"
  rm -f "${TMP_UPDATER_COPY}"
  # Copy before moving an existing stable updater. When this script is invoked
  # through that updater, BASH_SOURCE[0] points at the file being replaced.
  cp "${updater_source}" "${TMP_UPDATER_COPY}"
  chmod 0755 "${TMP_UPDATER_COPY}"
  mkdir -p "$(dirname "${STABLE_UPDATER}")"
  if [[ -e "${STABLE_UPDATER}" || -L "${STABLE_UPDATER}" ]]; then
    OLD_UPDATER_BACKUP="${STABLE_UPDATER}.previous.$$"
    [[ ! -e "${OLD_UPDATER_BACKUP}" ]] || fail "updater backup path already exists: ${OLD_UPDATER_BACKUP}"
    mv "${STABLE_UPDATER}" "${OLD_UPDATER_BACKUP}"
  else
    OLD_UPDATER_BACKUP="${STABLE_UPDATER}.absent.$$"
  fi
  mv -f "${TMP_UPDATER_COPY}" "${STABLE_UPDATER}"
  TMP_UPDATER_COPY=""
  ensure_symlink "${STABLE_UPDATER}" "${BIN_LINK}"
}

rollback() {
  local exit_code="$1"
  (( UPDATE_SUCCEEDED == 1 )) && return
  set +e
  restore_links
  if [[ -n "${OLD_UPDATER_BACKUP}" && -e "${OLD_UPDATER_BACKUP}" ]]; then
    rm -f "${STABLE_UPDATER}"
    mv "${OLD_UPDATER_BACKUP}" "${STABLE_UPDATER}"
  elif [[ -n "${OLD_UPDATER_BACKUP}" ]]; then
    rm -f "${STABLE_UPDATER}"
  fi
  if [[ -d "${REPO_DIR}" ]]; then
    rm -rf "${REPO_DIR}"
  fi
  if [[ -n "${OLD_REPO_BACKUP}" && -d "${OLD_REPO_BACKUP}" ]]; then
    mv "${OLD_REPO_BACKUP}" "${REPO_DIR}"
  fi
  if [[ -n "${TMP_CHECKOUT}" && -d "${TMP_CHECKOUT}" ]]; then
    rm -rf "${TMP_CHECKOUT}"
  fi
  if [[ -n "${TMP_UPDATER_COPY}" && -e "${TMP_UPDATER_COPY}" ]]; then
    rm -f "${TMP_UPDATER_COPY}"
  fi
  exit "${exit_code}"
}

trap 'rollback "$?"' EXIT

print_version() {
  local version
  local latest_commit

  version="$(git -C "${REPO_DIR}" describe --tags --always --dirty)"
  latest_commit="$(git -C "${REPO_DIR}" log -1 --format='%h %s (%cI)')"
  printf 'OpenClaw WEEX skills are ready.\n'
  printf 'Repository: %s\n' "${REPO_DIR}"
  printf 'Branch: %s\n' "${BRANCH}"
  printf 'Version: %s\n' "${version}"
  printf 'Latest commit: %s\n' "${latest_commit}"
  printf 'Update command: %s\n' "${BIN_LINK}"
}

main() {
  require_command git
  validate_source
  mkdir -p "$(dirname "${REPO_DIR}")" "${OPENCLAW_ROOT}"
  record_all_link_states
  update_repository
  link_skills
  validate_openclaw
  install_stable_updater

  rm -rf "${OLD_REPO_BACKUP}"
  [[ "${OLD_UPDATER_BACKUP}" == *.absent.$$ ]] || rm -f "${OLD_UPDATER_BACKUP}"
  UPDATE_SUCCEEDED=1
  print_version
}

main "$@"
