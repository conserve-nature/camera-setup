#!/bin/bash
# Online bootstrap. Also works when supplied directly to /bin/bash -c by curl.
set -euo pipefail

camera_setup_install() {
    set -euo pipefail
    umask 022
    local destination=/opt/trail-camera/camera-setup
    local origin=https://github.com/conserve-nature/camera-setup.git
    local path owner mode package status checkout_status
    local -a missing=()
    local -a apt_options=(
        -o DPkg::Lock::Timeout=600 -o Acquire::Retries=3
        -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60
        -o APT::Update::Error-Mode=any
    )

    if [[ ! -r /etc/os-release ]]; then
        echo 'This installer requires Debian or 64-bit Raspberry Pi OS.' >&2
        return 1
    fi
    . /etc/os-release
    if [[ ${ID:-} != debian ]]; then
        echo 'This installer requires Debian or 64-bit Raspberry Pi OS.' >&2
        return 1
    fi
    case "$(dpkg --print-architecture)" in
        arm64|amd64) ;;
        *) echo 'Supported architectures: arm64 and amd64.' >&2; return 1 ;;
    esac

    # The checkout executes as root. Never adopt a user-writable tree or follow
    # a symlink into one; fail instead of taking ownership of existing files.
    path=$destination
    while :; do
        if [[ -e $path || -L $path ]]; then
            if [[ -L $path || ! -d $path ]]; then
                echo "Expected a real directory, not a symlink: $path" >&2
                return 1
            fi
            read -r owner mode < <(stat -c '%u %a' "$path")
            if [[ $owner != 0 ]] || (( (8#$mode & 0022) != 0 )); then
                echo "Refusing non-root-owned or writable installation path: $path" >&2
                return 1
            fi
        fi
        [[ $path == / ]] && break
        path=$(dirname -- "$path")
    done
    if [[ -e $destination && ( ! -d $destination/.git || -L $destination/.git ) ]]; then
        echo "Destination already exists and is not a regular Git checkout: $destination" >&2
        return 1
    fi

    for package in git ca-certificates python3 gpgv debian-archive-keyring iproute2 procps util-linux; do
        status=$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)
        [[ $status == 'install ok installed' ]] || missing+=("$package")
    done
    if (( ${#missing[@]} )); then
        # Keep package hooks noninteractive and prevent needrestart from
        # automatically restarting services while preserving SSH access.
        export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none
        export NEEDRESTART_MODE=l UCF_FORCE_CONFFOLD=1
        apt-get update "${apt_options[@]}"
        apt-get install -y --no-install-recommends --no-upgrade "${apt_options[@]}" \
            -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
            "${missing[@]}"
    fi

    if [[ -d $destination/.git ]]; then
        if [[ $(git -C "$destination" remote get-url origin) != "$origin" ]]; then
            echo 'Existing checkout must use the official camera-setup origin.' >&2
            return 1
        fi
        if [[ $(git -C "$destination" symbolic-ref --quiet --short HEAD) != main ]]; then
            echo 'Existing checkout must be on branch main.' >&2
            return 1
        fi
        checkout_status=$(git -C "$destination" status --porcelain --untracked-files=all)
        if [[ -n $checkout_status ]]; then
            echo 'Existing checkout contains local changes; preserve or commit them before rerunning.' >&2
            return 1
        fi
        git -C "$destination" fetch --no-tags origin main
        if ! git -C "$destination" merge-base --is-ancestor HEAD FETCH_HEAD; then
            echo 'Existing checkout has local commits or has diverged; resolve it before rerunning.' >&2
            return 1
        fi
        git -C "$destination" merge --ff-only FETCH_HEAD
    else
        install -d -m 0755 "$(dirname -- "$destination")"
        git clone --branch main --single-branch "$origin" "$destination"
    fi
    /bin/bash "$destination/setup.sh"
    printf '%s\n' "Setup complete. Checkout: $destination"
    printf '%s\n' 'OS updates remain operator-triggered; setup does not start an upgrade or reboot.'
}

if [[ $(id -u) -eq 0 ]]; then
    camera_setup_install
else
    if ! command -v sudo >/dev/null; then
        echo 'Run this installer as root, or install sudo first.' >&2
        exit 1
    fi
    # Passing the function body works both from a file and from bash -c; there
    # is no downloaded temporary script whose path must survive sudo.
    sudo /bin/bash -c "$(declare -f camera_setup_install); camera_setup_install"
fi
