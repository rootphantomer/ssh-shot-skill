package font

import (
	_ "embed"

	"github.com/golang/freetype/truetype"
	"golang.org/x/image/font"
)

// 内嵌裁剪版等宽中文字体（Sarasa Mono SC Regular，GB2312 常用字集）。
// 四种字重都指向同一份字形：中文可读、颜色保留，粗体/斜体效果牺牲。
//
//go:embed SSHShotMonoSC-GB2312.ttf
var sshShotFont []byte

type sshShotFontType struct{}

// Hack 保持上游变量名，供 termshot 的 img.NewImageCreator() 直接调用。
var Hack Font = sshShotFontType{}

func (sshShotFontType) Regular(opts *truetype.Options) font.Face    { return face(sshShotFont, opts) }
func (sshShotFontType) Bold(opts *truetype.Options) font.Face       { return face(sshShotFont, opts) }
func (sshShotFontType) Italic(opts *truetype.Options) font.Face     { return face(sshShotFont, opts) }
func (sshShotFontType) BoldItalic(opts *truetype.Options) font.Face { return face(sshShotFont, opts) }
