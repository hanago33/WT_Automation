@echo off
echo ========================================
echo   WT Automation Website 上传工具
echo ========================================
echo.

cd /d D:\My_RF_Project\WT_Automation

echo [1/4] 检查 git 状态...
git status
echo.

echo [2/4] 添加 website 目录到暂存区...
git add website/
git add .github/workflows/deploy-website.yml
echo.

echo [3/4] 提交更改...
git commit -m "Add project website for GitHub Pages"
echo.

echo [4/4] 推送到 GitHub...
git push
echo.

echo ========================================
echo   完成！请打开以下地址配置 Pages：
echo   https://github.com/hanago33/WT_Automation/settings/pages
echo.
echo   配置方法：Source = Deploy from a branch
echo           Branch = main + /website
echo ========================================
echo.
pause