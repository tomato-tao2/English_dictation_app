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
