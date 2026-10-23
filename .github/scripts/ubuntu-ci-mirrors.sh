#!/usr/bin/env bash
set -euo pipefail
# Keep apt signature verification and the runner's configured suites/components.
# Avoid the Azure mirror's unbounded codec downloads in both media and browser jobs.
if [ -f /etc/apt/apt-mirrors.txt ]; then
  printf '%s\n' 'https://archive.ubuntu.com/ubuntu/' | sudo tee /etc/apt/apt-mirrors.txt >/dev/null
fi
if [ -f /etc/apt/apt-security-mirrors.txt ]; then
  printf '%s\n' 'https://security.ubuntu.com/ubuntu/' | sudo tee /etc/apt/apt-security-mirrors.txt >/dev/null
fi
printf '%s\n' 'Acquire::Retries "1";' 'Acquire::http::Timeout "20";' 'Acquire::https::Timeout "20";' |
  sudo tee /etc/apt/apt.conf.d/99-remask-ci-timeouts >/dev/null
