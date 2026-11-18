package converter

import (
	"fmt"
	"io"
	"strings"
)

var textSeparator = "\n\n" + strings.Repeat("━", 40) + "\n"

// WriteTXTStream writes chapters as they are produced, without book aggregation.
func WriteTXTStream(w io.Writer, source ChapterSource) error {
	return source(func(ch Chapter) error {
		if _, err := fmt.Fprintf(w, "\n%s\n\n", ch.Title); err != nil {
			return err
		}
		if _, err := io.WriteString(w, ch.Content); err != nil {
			return err
		}
		_, err := io.WriteString(w, textSeparator)
		return err
	})
}


// WriteTXTStreamWithIntro prepends the original 52shuku introduction as a
// distinct front section. It leaves chapter output identical to WriteTXTStream.
func WriteTXTStreamWithIntro(w io.Writer, meta BookMeta, intro string, source ChapterSource) error {
	if strings.TrimSpace(intro) != "" {
		if _, err := fmt.Fprintf(w, "《%s》\n作者：%s\n\n【小说简介】\n\n%s\n", meta.Title, meta.Author, strings.TrimSpace(intro)); err != nil {
			return err
		}
	}
	return WriteTXTStream(w, source)
}
