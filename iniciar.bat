@echo off
title beehus-swat
cd /d "%~dp0"

rem [2026-09-27, achado A4] O "git pull" a cada inicio atualiza quem roda o painel da branch do
rem time (development/main), mas numa branch de feature puxava/mesclava codigo no meio do trabalho.
rem Agora: so puxa em development ou main, com a arvore sem mudancas, e so avanca (--ff-only:
rem nunca cria merge nem conflito). Em qualquer outro caso avisa e sobe o servidor sem puxar.
set "BRANCH="
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "BRANCH=%%b"
if /i "%BRANCH%"=="development" goto :puxar
if /i "%BRANCH%"=="main" goto :puxar
echo [aviso] Branch atual: "%BRANCH%" - atualizacao automatica (git pull) pulada.
echo         Ela so roda em development ou main.
goto :iniciar

:puxar
git diff --quiet
if errorlevel 1 goto :sujo
git diff --cached --quiet
if errorlevel 1 goto :sujo
echo Atualizando codigo (%BRANCH%)...
git pull --ff-only
if errorlevel 1 echo [aviso] git pull nao conseguiu so avancar a branch - subindo com o codigo atual.
goto :iniciar

:sujo
echo [aviso] Ha mudancas locais nao commitadas - atualizacao automatica (git pull) pulada.

:iniciar
echo.
echo Iniciando servidor...
powershell -ExecutionPolicy Bypass -File "%~dp0start.ps1"
