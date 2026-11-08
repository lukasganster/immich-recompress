# Changelog

## [1.5.0](https://github.com/lukasganster/immich-recompress/compare/v1.4.1...v1.5.0) (2026-11-07)

### Features

* **grid:** use distinct sort icons for each direction ([20c6b87](https://github.com/lukasganster/immich-recompress/commit/20c6b87180012b9ad3a759d18509107904c8954a))
* **ui:** restyle navbar, dashboard and filter bar after immich ([34c4f2d](https://github.com/lukasganster/immich-recompress/commit/34c4f2d909b0b98dd041611a223431b10cf3dd07))
* **grid:** restyle media table as an immich list view ([2557932](https://github.com/lukasganster/immich-recompress/commit/25579327a1a06d1e975d3473d6ae87432a1dfe12))
* **bench:** restyle queue and settings panels as immich cards ([e8c6b21](https://github.com/lukasganster/immich-recompress/commit/e8c6b21fb24a01fbe8cdbfd132ce3600c23477ef))
* **browse:** restyle select media dialog after immich search filters ([b090274](https://github.com/lukasganster/immich-recompress/commit/b090274c95d26f5aa73cb318d885b8d7f1f66406))
* **review:** restyle detail drawer and review modal after immich ([3001218](https://github.com/lukasganster/immich-recompress/commit/30012184484cf6a04551745b9a6c200466405ef4))

### Bug Fixes

* **grid:** toggle sort between ascending and descending ([cfd5b28](https://github.com/lukasganster/immich-recompress/commit/cfd5b28c5e3392786a50267f68b6ee6ba1c7c90d))
* **layout:** keep the media table visible when the bench stacks ([d792c43](https://github.com/lukasganster/immich-recompress/commit/d792c43fa71adbf909390fe2a077d5986451415a))
* **grid:** make row actions clickable and show that the list scrolls ([ff2075f](https://github.com/lukasganster/immich-recompress/commit/ff2075f73976256618a8152bca2d49b980401e4a))
* **a11y:** restore focus rings and raise light-theme contrast ([4d2e37a](https://github.com/lukasganster/immich-recompress/commit/4d2e37a18babc2cf1f8d7384e117281281a56803))
* **review:** match the full preview to the bench decision ([57603f1](https://github.com/lukasganster/immich-recompress/commit/57603f100d6203ff9f3db1507c7a9eaedd804a01))
* **ui:** format dates the same way everywhere ([7314317](https://github.com/lukasganster/immich-recompress/commit/731431790a99acde5534a6cb242a90b938ac21b0))

## [1.4.1](https://github.com/lukasganster/immich-recompress/compare/v1.4.0...v1.4.1) (2026-10-31)


### Bug Fixes

* **jobs:** delete local backup after replacement ([#40](https://github.com/lukasganster/immich-recompress/issues/40)) ([0646a3e](https://github.com/lukasganster/immich-recompress/commit/0646a3e473bfd4adcf00d0d2ed3ab42cb1621af4))

## [1.4.0](https://github.com/lukasganster/immich-recompress/compare/v1.3.2...v1.4.0) (2026-10-31)


### Features

* **brand:** replace logo ([7b760d8](https://github.com/lukasganster/immich-recompress/commit/7b760d88eb9d5fda308bc91d41e4fd3b0638a697))
* **browse:** collapse metadata filters into sections ([7b760d8](https://github.com/lukasganster/immich-recompress/commit/7b760d88eb9d5fda308bc91d41e4fd3b0638a697))
* **detail:** play videos in the details drawer ([7b760d8](https://github.com/lukasganster/immich-recompress/commit/7b760d88eb9d5fda308bc91d41e4fd3b0638a697))
* **queue:** add replace all media action ([7b760d8](https://github.com/lukasganster/immich-recompress/commit/7b760d88eb9d5fda308bc91d41e4fd3b0638a697))
* **settings:** move settings to the rail and group them by media type ([7b760d8](https://github.com/lukasganster/immich-recompress/commit/7b760d88eb9d5fda308bc91d41e4fd3b0638a697))


### Bug Fixes

* **deps:** pin node to 22.23.4 ([7b760d8](https://github.com/lukasganster/immich-recompress/commit/7b760d88eb9d5fda308bc91d41e4fd3b0638a697))
* **grid:** fill available width and keep state on one line ([7b760d8](https://github.com/lukasganster/immich-recompress/commit/7b760d88eb9d5fda308bc91d41e4fd3b0638a697))

## [1.3.2](https://github.com/lukasganster/immich-recompress/compare/v1.3.1...v1.3.2) (2026-10-20)


### Features

* **browse:** add album and metadata filters ([756921b](https://github.com/lukasganster/immich-recompress/commit/756921b155f4b373853ee9a8f594c3ee656c5a92)), closes [#13](https://github.com/lukasganster/immich-recompress/issues/13)
* **photos:** add lossless jpeg xl output ([3485471](https://github.com/lukasganster/immich-recompress/commit/348547161e727ed84228b5bb048326250c1c70ab))


### Bug Fixes

* **live-photo:** abort when hiding motion clip fails ([ff53a1e](https://github.com/lukasganster/immich-recompress/commit/ff53a1eef2de817e3e304b7ffcb1c3d17c8f587f)), closes [#15](https://github.com/lukasganster/immich-recompress/issues/15)


### Miscellaneous Chores

* **release:** prepare 1.3.2 patch release ([b355164](https://github.com/lukasganster/immich-recompress/commit/b3551649762a73f961660283b7a0689fceb53903))
