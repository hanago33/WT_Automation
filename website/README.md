# WT Automation 项目网站

这是 WT Automation 项目的官方展示网站，基于项目内容构建，介绍项目的核心功能、技术架构、关键模块和成果数据。

> **版权所有**：中车株洲电力机车研究所有限公司
> **项目主持**：廖锦朋（风电事业部风资源技术中心）
> **联系方式**：liaojinpeng@teg.cn / 18075742692

---

## 文件结构

```
website/
├── index.html      # 主页面（单页应用，共9部分）
├── styles.css      # 样式文件（中车配色，响应式设计）
├── script.js       # 交互脚本（滚动动画、导航等）
└── README.md       # 本说明文档
```

---

## 网站内容

网站包含以下 **9 个主要部分**：

1. **首页英雄区** - 项目概述与核心价值展示
2. **项目简介** - WT Automation 的定位与三层执行策略
3. **问题与解决方案** - 分析痛点并展示结构化 + AI 兜底的解决方案
4. **核心功能** - 智能控件定位、可视化流程编辑、结构化执行引擎、运行报告系统
5. **技术架构** - 5 层模块化架构的详细说明
6. **关键模块** - 6 个核心 Python 模块的功能介绍与代码示例
7. **成果展示** - 可量化的运行数据：46 步流程 5/5 成功、平均 119.597s 等
8. **快速开始** - 三步上手指南与项目统计
9. **联系我们** - 版权信息、项目主持人、邮箱电话、中车品牌展示

---

## 配色方案（中车株洲所品牌配色）

网站采用 **中国红 + 深蓝** 的中车品牌配色方案：

| 颜色 | 色值 | 用途 |
|------|------|------|
| 中国红 | `#D71921` | 主色调 - 按钮、链接、强调色 |
| 深蓝色 | `#1E3A8A` | 次色调 - 辅助强调、品牌标识 |
| 浅红色 | `#F8D7DA` | 背景色、悬停态、边框 |
| 深灰 | `#1F2937` | 正文文字 |
| 中灰 | `#4B5563` | 次要文字 |
| 浅灰 | `#F5F5F7` | 页面背景 |

### 渐变组合

- **主渐变**：中国红 → 深蓝（`linear-gradient(135deg, #D71921 0%, #1E3A8A 100%)`）
- **次渐变**：深蓝 → 亮蓝（用于装饰元素）
- **背景渐变**：浅灰渐变（用于分区背景）

如需修改配色，请编辑 `styles.css` 文件顶部的 `:root` 选择器中的 CSS 变量。

---

## 技术特性

- ✅ **响应式设计** - 适配桌面、平板、手机等多种设备
- ✅ **现代 UI 风格** - 渐变色、卡片、阴影等现代设计元素
- ✅ **交互动画** - 滚动动画、悬停效果、数字动画等
- ✅ **代码高亮** - JSON/伪代码示例的语法高亮显示
- ✅ **纯静态实现** - 无需后端，直接在浏览器打开即可运行
- ✅ **中车品牌配色** - 中国红 + 深蓝的专业企业配色

---

## 如何查看

### 方法 1：直接打开（简单推荐）

直接在文件管理器中双击 `index.html` 文件，或在浏览器地址栏输入文件路径：

```
file:///D:/My_RF_Project/WT_Automation/website/index.html
```

---

## 方法 2：本地访问（本机查看）

使用 Python 启动本地服务器（**最简单、推荐**）：

```powershell
cd D:\My_RF_Project\WT_Automation\website
python -m http.server 8000
```

然后在本机浏览器打开：

```
http://localhost:8000
```

或：

```
http://127.0.0.1:8000
```

---

## 方法 3：局域网访问（同局域网内其他电脑查看）

### 步骤 1：查看本机 IP 地址

在命令行（PowerShell 或 CMD）执行：

```powershell
ipconfig
```

找到"IPv4 地址"，例如：`192.168.1.100`

```
无线局域网适配器 Wi-Fi:
   IPv4 地址 . . . . . . . . . . . . : 192.168.1.100  ← 你的局域网 IP
```

### 步骤 2：启动服务器时绑定所有网络接口

```powershell
cd D:\My_RF_Project\WT_Automation\website
python -m http.server 8000 --bind 0.0.0.0
```

或简写：

```powershell
python -m http.server 8000
```

> 注：在大多数系统上，不加 `--bind` 默认也会监听所有网络接口。

### 步骤 3：其他电脑访问

在同一局域网内的其他电脑/手机浏览器打开：

```
http://你的IP地址:8000
```

例如，如果你的 IP 是 `192.168.1.100`：

```
http://192.168.1.100:8000
```

### 注意事项

1. **Windows 防火墙**：首次启动时可能会弹出 Windows 安全警报，请选择"**允许访问**"并勾选"专用网络"。
2. **端口冲突**：如果 8000 端口被占用，可改用其他端口（如 3000、8080、5000 等）。
3. **保持电脑开启**：提供服务的电脑需要保持开机，Python 服务器进程不能关闭。
4. **局域网限制**：确保所有设备都连接在同一 Wi-Fi/局域网下。

---

## 方法 4：公网访问（外部网络/手机流量查看）

### 方案 A：内网穿透工具（推荐快速分享用）

使用免费工具如 **ngrok** 或 **frp** 将本地端口映射到公网：

#### 1. 使用 ngrok（简单推荐）

1. 访问 [https://ngrok.com](https://ngrok.com) 注册账号并下载
2. 解压后在命令行执行：

```powershell
# 先启动本地服务器
cd D:\My_RF_Project\WT_Automation\website
python -m http.server 8000

# 再打开一个新的命令行窗口，执行
ngrok http 8000
```

3. ngrok 会显示一个公网地址，如：

```
Forwarding  http://abc123.ngrok-free.app -> http://localhost:8000
```

4. 任何人在任何地方都可以通过这个地址访问你的网站。

#### 2. 使用 cpolar（国内用户推荐）

1. 访问 [https://www.cpolar.com](https://www.cpolar.com) 注册下载
2. 启动命令：

```powershell
cpolar http 8000
```

会生成类似 `https://xxxx.cpolar.io` 的公网地址。

---

### 方案 B：免费静态托管平台（长期推荐）

#### 1. GitHub Pages（全球通用）

1. 在 [https://github.com](https://github.com) 注册账号并创建新仓库
2. 将 `website` 文件夹内的所有文件上传到仓库根目录
3. 在仓库设置中启用 GitHub Pages，选择 `main` 分支
4. 获得访问地址如：`https://你的用户名.github.io/仓库名/`

#### 2. Gitee Pages（国内推荐，速度快）

1. 在 [https://gitee.com](https://gitee.com) 注册账号
2. 上传 `website` 文件夹内容到新仓库
3. 在服务菜单启用 Gitee Pages
4. 获得访问地址如：`https://你的用户名.gitee.io/仓库名/`

#### 3. Vercel（推荐用于演示）

1. 在 [https://vercel.com](https://vercel.com) 注册
2. 将 `website` 文件夹拖拽到网站上即可部署
3. 获得访问地址如：`https://你的项目名.vercel.app`

#### 4. Netlify（一键部署）

1. 在 [https://netlify.com](https://netlify.com) 注册
2. 将 `website` 文件夹直接拖拽到页面部署区
3. 获得访问地址如：`https://你的项目名.netlify.app`

---

### 方案 C：部署到企业内部服务器

如果部署到公司内部服务器，推荐以下方式：

#### 1. IIS（Windows 服务器）

1. 在服务器上安装 IIS（Windows 功能启用）
2. 将 `website` 文件夹文件复制到 `C:\inetpub\wwwroot\wt-automation\`
3. 在 IIS 管理器中创建新网站，指向该目录
4. 内部网络通过服务器 IP 访问：`http://服务器IP/wt-automation/`

#### 2. Nginx（跨平台）

1. 安装 Nginx
2. 修改 `nginx.conf` 添加配置：

```nginx
server {
    listen 80;
    server_name 你的域名或服务器IP;
    root D:/My_RF_Project/WT_Automation/website;
    index index.html;
}
```

3. 启动 Nginx：`nginx.exe`
4. 内部网络访问：`http://服务器IP/`

#### 3. Apache（跨平台）

1. 安装 Apache HTTP Server
2. 在 `httpd.conf` 配置 DocumentRoot
3. 启动 Apache 服务

---

## 快速部署命令总览

```powershell
# 1. 本地开发测试（本机访问）
cd D:\My_RF_Project\WT_Automation\website
python -m http.server 8000
# → 本机访问: http://localhost:8000

# 2. 局域网分享（同事电脑访问）
# 先查看 IP: ipconfig
# 然后: python -m http.server 8000
# → 同事访问: http://你的IP:8000

# 3. 公网分享（外部访问）
# 使用 ngrok:
# ngrok http 8000
# → 生成公网地址如: https://xxxx.ngrok-free.app
```

---

## 部署方式对比

| 部署方式 | 使用场景 | 难度 | 成本 | 访问速度 |
|---------|---------|------|------|---------|
| 双击打开 | 本机测试 | ⭐ 简单 | 免费 | 即时 |
| Python 本地服务器 | 开发测试 | ⭐ 简单 | 免费 | 即时 |
| 局域网分享 | 公司内部同事 | ⭐⭐ 简单 | 免费 | 取决于内网 |
| ngrok/cpolar | 快速对外分享演示 | ⭐⭐ 简单 | 免费版有限速 | 较慢 |
| GitHub Pages | 开源项目长期部署 | ⭐⭐ 中等 | 免费 | 国外较慢 |
| Gitee Pages | 国内项目长期部署 | ⭐⭐ 中等 | 免费 | 国内较快 |
| Vercel/Netlify | 快速一键部署 | ⭐⭐ 简单 | 免费版够用 | 较快 |
| 企业内网服务器 | 公司内部正式部署 | ⭐⭐⭐ 需要 IT 支持 | 视情况 | 内网最快 |

---

## 自定义修改

### 修改项目数据

编辑 `index.html` 文件中对应部分：

- 成果数据 → 搜索 `contact-grid` 或 `metric-card` 部分
- 核心功能 → 搜索 `feature-card` 部分
- 技术架构 → 搜索 `architecture-diagram` 部分
- 联系方式 → 搜索 `contact-grid` 部分（包含姓名、邮箱、电话）

### 修改配色方案

编辑 `styles.css` 文件顶部的 `:root` 选择器，修改 CSS 变量：

```css
:root {
    --color-primary: #D71921;      /* 中车中国红 */
    --color-secondary: #1E3A8A;    /* 中车深蓝色 */
    /* ... 其他变量 */
}
```

### 修改版权和联系信息

在 `index.html` 搜索以下内容进行编辑：

- 联系我们卡片：搜索 `contact-grid`
- 页脚版权声明：搜索 `footer-bottom`
- 页面 meta 信息：搜索 `<meta name="author"`

当前的联系信息为：

| 项目 | 内容 |
|------|------|
| 版权所属 | 中车株洲电力机车研究所有限公司 |
| 部门 | 风电事业部 · 风资源技术中心 |
| 项目主持人 | 廖锦朋 |
| 邮箱 | liaojinpeng@teg.cn |
| 电话 | 18075742692 |

### 添加新的模块介绍

在 `index.html` 的 `modules` 区域（关键模块部分）复制一个现有的 `module-detail-card` 模板，修改内容即可。

---

## 浏览器兼容性

- ✅ Chrome / Edge 90+
- ✅ Firefox 88+
- ✅ Safari 14+
- ✅ Opera 76+
- ✅ 手机浏览器（iOS Safari、Android Chrome 等）

需要支持较旧浏览器时，可考虑添加 Babel 转译和 Polyfill。

---

## 与项目的关联

网站内容基于以下实际项目文件：

- 项目架构参考：`PROJECT_ARCHITECTURE.md`
- 项目讲解资料参考：`docs/WT_Automation_项目讲解材料_讲稿_更新版.md`
- 项目结构参考：`docs/项目结构树_精简美化版.md`
- 核心代码参考：根目录下的各个 `.py` 文件

网站中的数据（如 46 步流程、119.597s 耗时等）均来自项目实际运行结果。

---

## 版权声明

© 2026 中车株洲电力机车研究所有限公司 · 风电事业部风资源技术中心

**项目主持人**：廖锦朋

**联系方式**：

- 邮箱：liaojinpeng@teg.cn
- 电话：18075742692

本网站内容用于风资源仿真技术的自动化项目展示，未经允许不得转载。

---

## 技术支持

如遇网站部署或访问问题，可联系项目负责人寻求技术支持。

---

## License

与 WT Automation 主项目保持一致，版权归中车株洲电力机车研究所有限公司所有。