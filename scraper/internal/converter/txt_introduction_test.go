package converter

import (
 "bytes"
 "strings"
 "testing"
)

func TestWriteTXTStreamWithIntro(t *testing.T) {
 var buf bytes.Buffer
 meta := BookMeta{Title:"某书", Author:"某人"}
 source := ChapterSource(func(yield func(Chapter) error) error {
  return yield(Chapter{Title:"第1章", Content:"正文第一段"})
 })
 if err := WriteTXTStreamWithIntro(&buf, meta, "简介一行\n小剧场：角色甲", source); err != nil { t.Fatal(err) }
 got := buf.String()
 for _, part := range []string{"《某书》","作者：某人","【小说简介】","简介一行","小剧场：角色甲","第1章","正文第一段"} {
  if !strings.Contains(got, part) { t.Fatalf("missing %q: %q",part,got) }
 }
 if strings.Count(got, "正文第一段") != 1 || strings.Count(got, "第1章") != 1 {
  t.Fatalf("duplicate chapter: %q",got)
 }
}
