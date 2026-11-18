# YuriScraper origin and attribution

This subtree was copied from the YuriScraper project:

- Original repository: https://github.com/Eurekaimer/yuri_scraper
- Exact baseline commit: 4771d7ef22ee1c2116beecccc9636f9d222e798c
- Project version: v0.7.0
- Stated upstream license: MIT (README.md, License section).
- Source history: https://github.com/Eurekaimer/yuri_scraper/commit/4771d7ef22ee1c2116beecccc9636f9d222e798c

The original repository at this commit did not contain a LICENSE file, but
its README declared the project as MIT. The standard MIT license text is
recorded in LICENSE next to this notice. Original documentation and source
headers were preserved. Keep original author attribution intact.

Local changes initially integrated into this copy:

- 52shuku.GetBookIntroduction extracts the full pre-chapter description,
  including the mini-theater, tags, and author notes.
- TXT output prepends that description once; EPUB chapter handling is kept.
- New Go regression tests cover both extraction and TXT output.

Before the migration, these changes lived in
kezish/nixos-config/modules/yuri-scraper/patches/preserve-txt-introduction.patch.

The scraper is maintained inside ks-novels, independent of a continuous
upstream merge relationship. Upstream code can be reviewed manually later.
