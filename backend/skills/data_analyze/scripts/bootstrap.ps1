# baa-basic environment check script (Windows / PowerShell)
# Zero-dependency: uses only built-in PowerShell, no python/node required to run.
#
# NOTE: This file is intentionally kept ASCII-only (no Chinese, no emoji).
#   Windows PowerShell 5.1 decodes a BOM-less .ps1 using the system ANSI code
#   page (GBK on zh-CN Windows), not UTF-8. Non-ASCII bytes then get misread as
#   syntax characters (stray '}', unterminated strings). Keeping the source
#   pure ASCII makes parsing robust regardless of BOM / code page.
#
# Responsibility: only detect whether python and node(>=14) are ready. When
#   missing, print READY=false plus the missing items. It no longer auto-installs
#   -- the upper-layer Agent guides the user to install based on the agent env
#   (catdesk / others). See references/environment-setup.md.
#
# Output contract (written to stdout for the Agent to parse; human-readable
# hints go to stderr):
#   BAA_BOOTSTRAP_BEGIN
#   OS=windows
#   PYTHON_CMD=python|py|         # empty=missing; py means invoke via `py -3`
#   PYTHON_VERSION=3.12.3|
#   NODE_OK=true|false
#   NODE_VERSION=v18.17.0|
#   READY=true|false
#   MANUAL_ACTIONS=Missing Python 3;Missing Node.js(>=14)   # missing-item desc, may be empty
#   BAA_BOOTSTRAP_END
#
# Exit codes: 0=READY  10=missing python  11=missing node  12=both missing
#
# Invocation: powershell -ExecutionPolicy Bypass -File <SKILL_DIR>\scripts\bootstrap.ps1

# -- First: force console and output to UTF-8 to avoid garbled output --
try {
    $OutputEncoding = [System.Text.UTF8Encoding]::new()
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    chcp 65001 > $null 2>&1
} catch { }

$ErrorActionPreference = 'SilentlyContinue'
$NODE_MIN_MAJOR = 14

function Log([string]$msg) { [Console]::Error.WriteLine($msg) }

$PYTHON_CMD = ''
$PYTHON_VERSION = ''
$NODE_OK = 'false'
$NODE_VERSION = ''
$MISSING = @()

# -- Detect python --------------------------------------------------------
# Default command on Windows is python; some envs only have the py launcher (needs py -3).
function Detect-Python {
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p) {
        $ver = & python -c "import sys;print('%d.%d.%d'%sys.version_info[:3])" 2>$null
        if ($ver -and $ver.StartsWith('3.')) {
            $script:PYTHON_CMD = 'python'
            $script:PYTHON_VERSION = $ver
            return $true
        }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $ver = & py -3 -c "import sys;print('%d.%d.%d'%sys.version_info[:3])" 2>$null
        if ($ver -and $ver.StartsWith('3.')) {
            $script:PYTHON_CMD = 'py'   # Agent must invoke via `py -3`
            $script:PYTHON_VERSION = $ver
            return $true
        }
    }
    return $false
}

# -- Detect node(>=NODE_MIN_MAJOR) ---------------------------------------
function Detect-Node {
    $n = Get-Command node -ErrorAction SilentlyContinue
    if ($n) {
        $script:NODE_VERSION = (& node -v 2>$null)   # e.g. v18.17.0
        if ($script:NODE_VERSION -match '^v(\d+)\.') {
            $major = [int]$Matches[1]
            if ($major -ge $NODE_MIN_MAJOR) {
                $script:NODE_OK = 'true'
                return $true
            }
        }
    }
    return $false
}

# -- Run ------------------------------------------------------------------
Log "----------------------------------------"
Log "baa-basic environment check (OS=windows)"
Log "----------------------------------------"

$pyOk = Detect-Python
if (-not $pyOk) { Log "[X] python not found"; $script:MISSING += 'Missing Python 3' }
$nodeOk = Detect-Node
if (-not $nodeOk) { Log "[X] node not found or version < $NODE_MIN_MAJOR"; $script:MISSING += 'Missing Node.js(>=14)' }

$READY = if ($pyOk -and $nodeOk) { 'true' } else { 'false' }
$manualStr = ($MISSING -join ';')

# -- Output contract block ------------------------------------------------
$out = @(
    'BAA_BOOTSTRAP_BEGIN'
    'OS=windows'
    "PYTHON_CMD=$PYTHON_CMD"
    "PYTHON_VERSION=$PYTHON_VERSION"
    "NODE_OK=$NODE_OK"
    "NODE_VERSION=$NODE_VERSION"
    "READY=$READY"
    "MANUAL_ACTIONS=$manualStr"
    'BAA_BOOTSTRAP_END'
)
$out | ForEach-Object { [Console]::Out.WriteLine($_) }

# -- Exit code ------------------------------------------------------------
if ($READY -eq 'true') { exit 0 }
if (-not $pyOk -and -not $nodeOk) { exit 12 }
if (-not $pyOk) { exit 10 }
exit 11
