英语单词听写 — 本地打开网页

一、必须运行的文件
  项目根目录下的：  web_app.py

二、启动命令（任选一种）
  1）双击：启动网站.bat
  2）在「命令提示符」里先进入本项目文件夹，再输入：
        python web_app.py

三、看到黑窗口里出现
        Running on http://127.0.0.1:5000
     再在浏览器地址栏打开：
        http://127.0.0.1:5000/dictation

四、若 /dictation 显示 404
  先打开：http://127.0.0.1:5000/_debug/who
  若这里也是 404，说明 5000 端口上不是本程序，请关掉其它 python 后重试。

五、不要用错文件
  必须运行 web_app.py。不要运行 main.py（那是桌面窗口版）、dictation_core.py 等。

六、多用户登录与数据库（可选，适合云服务器）
  默认与以前一样：不设环境变量则无需账号，进度仍写在项目根目录的 progress.json。
  若要在阿里云等多用户环境使用「注册 / 登录」并把每人进度、错题、个人词库存进数据库：
    1）安装依赖：pip install -r requirements.txt
    2）启动前设置环境变量（Windows PowerShell 示例）：
         $env:USE_USER_ACCOUNTS="1"
    3）数据库：
         - 本地默认：自动使用项目下 instance\dictation_users.db（SQLite），无需额外安装数据库软件。
         - 阿里云 RDS（PostgreSQL）：再安装 requirements-cloud.txt 里的 psycopg，并设置例如
           DATABASE_URL=postgresql+psycopg://用户名:密码@主机:5432/数据库名
    4）仍可与「站点访问密码」同时使用：在登录页会先校验 DICTATION_WEB_PASSWORD（若已配置），再校验账号密码。
