# 内置字体说明

`SSHShotMonoSC-GB2312.ttf` 是从 **Sarasa Mono SC (更纱黑体 SC) Regular** 裁剪而来的等宽中文字体，供 `scripts/build_termshot.sh` 内嵌到 `termshot-cjk` 中，解决 termshot 默认 Hack 字体不含中文字形、截图里中文变方框的问题。

- 上游：https://github.com/be5invis/Sarasa-Gothic （发布包 `SarasaMonoSC-TTF-Unhinted-1.0.43.7z`）
- 授权：SIL Open Font License 1.1，全文见同目录 [`OFL.txt`](OFL.txt)
- 裁剪字符集：ASCII 可打印字符 + GB2312 全部字符（6763 汉字 + 符号，共 7597 个码位）+ 制表符与常用箭头/勾叉符号
- 字体内部名称已改为 `SSHShot Mono SC`，以区别于上游字体
- 原始 Regular TTF 13.4MB → 裁剪后 2.1MB
- 四种字重均指向这一份字形：**中文可读、ANSI 颜色保留，但粗体/斜体效果不再区分**

覆盖范围是 GB2312；生僻字、繁体字、日文假名等不在字符集中的字符仍会显示为方框。如需扩大覆盖，可按同样方式改用 GBK 字符集重新裁剪（单字重约 5–6MB）。
