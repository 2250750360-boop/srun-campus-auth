@echo off
:: 安装计划任务（自动请求管理员权限）
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"\"%~dp0install_task.ps1\"\"' -Verb RunAs"
