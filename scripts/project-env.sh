#!/usr/bin/env bash

set -euo pipefail

export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"

if [[ -d /opt/homebrew/opt/node@22/bin ]]; then
  export PATH="/opt/homebrew/opt/node@22/bin:$PATH"
fi

export PATH="$HOME/.local/bin:$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
