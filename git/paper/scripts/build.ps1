param([switch]$DraftCopy)
$ErrorActionPreference = 'Stop'
$paperRoot = Split-Path $PSScriptRoot -Parent
Push-Location $paperRoot
try {
    New-Item -ItemType Directory -Force -Path build | Out-Null
    & pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main.tex
    if ($LASTEXITCODE -ne 0) { throw 'First LaTeX pass failed.' }
    & bibtex build/main
    if ($LASTEXITCODE -ne 0) { throw 'BibTeX failed.' }
    & pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main.tex
    if ($LASTEXITCODE -ne 0) { throw 'Second LaTeX pass failed.' }
    & pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main.tex
    if ($LASTEXITCODE -ne 0) { throw 'Final LaTeX pass failed.' }
    if ($DraftCopy) {
        $draftDir = Join-Path $paperRoot '../../drafts'
        New-Item -ItemType Directory -Force -Path $draftDir | Out-Null
        $draftBase = 'docspectra-icsc-' + (Get-Date -Format 'yyyy-MM-dd') + '-working'
        $draftPath = Join-Path $draftDir ($draftBase + '.pdf')
        $draftIndex = 2
        while (Test-Path -LiteralPath $draftPath) {
            $draftPath = Join-Path $draftDir ($draftBase + '-' + $draftIndex + '.pdf')
            $draftIndex++
        }
        Copy-Item -LiteralPath build/main.pdf -Destination $draftPath
        Write-Output ('Draft copy: ' + $draftPath)
    }
} finally { Pop-Location }
