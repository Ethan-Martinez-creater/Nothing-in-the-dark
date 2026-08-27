# Vendored dependencies

## MediaCrawler

- Source: <https://github.com/NanmiCoder/MediaCrawler>
- Pinned commit: `17f66121e0fcc40fc23958b995bec873d422667d`
- Retrieved: 2026-07-27
- License: `NON-COMMERCIAL LEARNING LICENSE 1.1`

The COIFESP backend invokes MediaCrawler through a subprocess adapter. This workstation
uses `E:\miniconda3\envs\bettafish\python.exe` for both the backend and MediaCrawler;
the setup script installs the backend package into that explicitly selected environment.

Before updating the dependency, rerun the backend adapter tests and manually verify
the CLI flags and JSONL schemas for Weibo and Bilibili.

## Local compatibility patches

The pinned source currently carries local patches for the 2026-07/08 platform pages.
Their complete, reviewable diff is tracked in `vendor/mediacrawler-local.patch` and can
be replayed from a clean pinned checkout with `scripts/apply-mediacrawler-patches.cmd`:

- current Weibo, Bilibili, Tieba, Zhihu and Douyin login selectors/flows;
- desktop user-agent for the Weibo SSO login page;
- `domcontentloaded` navigation for continuously loading home pages;
- strict enforcement of `CRAWLER_MAX_NOTES_COUNT` below platform page sizes;
- Douyin system-Chrome fallback and redirect-safe user-agent acquisition;
- Zhihu comment-count enforcement and API compatibility fixes.

Re-export `vendor/mediacrawler-local.patch` whenever the nested working tree changes,
and recheck all five platforms whenever the upstream dependency is updated.
