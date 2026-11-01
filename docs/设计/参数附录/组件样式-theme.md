# 组件样式：theme.css

由 `更新参数索引.py` 从独立资源生成。数值是现状记录，不是每项都允许自由修改；语义与约束见《参数与动效》。

| 名称／属性 | 值 | 条件／选择器 | 来源 |
|---|---|---|---|
| `syntax` | `'<length>'` | `@property --theme-reveal-radius` | 预览资源/css/theme.css:20 |
| `inherits` | `false` | `@property --theme-reveal-radius` | 预览资源/css/theme.css:20 |
| `initial-value` | `-24px` | `@property --theme-reveal-radius` | 预览资源/css/theme.css:20 |
| `view-transition-name` | `none` | `.is-theme-transitioning` | 预览资源/css/theme.css:21 |
| `animation` | `none` | `::view-transition-old(*), ::view-transition-new(*)` | 预览资源/css/theme.css:22 |
| `mix-blend-mode` | `normal` | `::view-transition-old(*), ::view-transition-new(*)` | 预览资源/css/theme.css:22 |
| `mask-image` | `radial-gradient(circle at var(--theme-reveal-x, 50%) var(--theme-reveal-y, 42px),     #000 max(0px, calc(var(--theme-reveal-radius) - 24px)),     transparent max(0px, calc(var(--theme-reveal-radius) + 24px)))` | `::view-transition-new(*)` | 预览资源/css/theme.css:24 |
| `mask-image` | `radial-gradient(circle at var(--theme-reveal-x, 50%) var(--theme-reveal-y, 42px),     transparent max(0px, calc(var(--theme-reveal-radius) - 24px)),     #000 max(0px, calc(var(--theme-reveal-radius) + 24px)))` | `::view-transition-old(*), .theme-background` | 预览资源/css/theme.css:29 |
| `position` | `fixed` | `.theme-background` | 预览资源/css/theme.css:33 |
| `inset` | `0` | `.theme-background` | 预览资源/css/theme.css:33 |
| `z-index` | `0` | `.theme-background` | 预览资源/css/theme.css:33 |
| `pointer-events` | `none` | `.theme-background` | 预览资源/css/theme.css:33 |
| `overflow` | `hidden` | `.theme-background` | 预览资源/css/theme.css:33 |
| `transition` | `none !important` | `:root:active-view-transition, :root:active-view-transition *` | 预览资源/css/theme.css:34 |
| `background-image` | `radial-gradient(ellipse 64% 54% at 53% 35%, rgba(252, 253, 250, .76), transparent 70%),     radial-gradient(ellipse 32% 24% at 53% 33%, rgba(232, 176, 42, .07), transparent 68%)` | `[data-palette="jade"] body` | 预览资源/css/theme.css:103 |
| `background-image` | `radial-gradient(ellipse 58% 48% at 53% 35%, rgba(16, 54, 43, .42), transparent 70%),     radial-gradient(ellipse 30% 22% at 53% 33%, rgba(168, 118, 22, .09), transparent 68%)` | `[data-theme="night"][data-palette="jade"] body` | 预览资源/css/theme.css:146 |
| `opacity` | `.16` | `[data-palette="jade"] .aura .grain` | 预览资源/css/theme.css:152 |
| `display` | `none` | `[data-theme="night"][data-palette="jade"] .aura .grain` | 预览资源/css/theme.css:153 |
| `opacity` | `.3` | `[data-palette="jade"] .aura i` | 预览资源/css/theme.css:154 |
| `filter` | `blur(96px)` | `[data-palette="jade"] .aura i` | 预览资源/css/theme.css:154 |
| `opacity` | `.24` | `[data-theme="night"][data-palette="jade"] .aura i` | 预览资源/css/theme.css:155 |
| `background` | `linear-gradient(135deg, var(--jade-deep), var(--jade))` | `[data-palette="jade"] .msg-user .bubble` | 预览资源/css/theme.css:158 |
| `color` | `#fff` | `[data-palette="jade"] .msg-user .bubble` | 预览资源/css/theme.css:159 |
| `box-shadow` | `0 10px 24px -12px color-mix(in srgb, var(--jade) 52%, transparent)` | `[data-palette="jade"] .msg-user .bubble` | 预览资源/css/theme.css:160 |
| `background` | `linear-gradient(135deg, #127d65, #1ea889)` | `[data-theme="night"][data-palette="jade"] .msg-user .bubble` | 预览资源/css/theme.css:163 |
| `color` | `#f4fbf8` | `[data-theme="night"][data-palette="jade"] .msg-user .bubble` | 预览资源/css/theme.css:164 |
| `background-image` | `radial-gradient(ellipse 68% 58% at 54% 38%, rgba(248, 251, 255, .78), transparent 72%),     radial-gradient(ellipse 56% 48% at 86% 88%, rgba(164, 146, 248, .16), transparent 70%)` | `[data-palette="cool"] body` | 预览资源/css/theme.css:217 |
| `background-image` | `radial-gradient(ellipse 64% 54% at 54% 38%, rgba(26, 42, 88, .36), transparent 72%),     radial-gradient(ellipse 52% 46% at 86% 88%, rgba(68, 42, 112, .22), transparent 70%)` | `[data-theme="night"][data-palette="cool"] body` | 预览资源/css/theme.css:260 |
| `display` | `none` | `[data-palette="cool"] .aura .grain` | 预览资源/css/theme.css:265 |
| `opacity` | `.34` | `[data-palette="cool"] .aura i` | 预览资源/css/theme.css:266 |
| `filter` | `blur(92px)` | `[data-palette="cool"] .aura i` | 预览资源/css/theme.css:266 |
| `opacity` | `.28` | `[data-theme="night"][data-palette="cool"] .aura i` | 预览资源/css/theme.css:267 |
| `background` | `linear-gradient(135deg, var(--jade), color-mix(in srgb, var(--jade) 66%, var(--cinnabar)))` | `[data-palette="cool"] .msg-user .bubble` | 预览资源/css/theme.css:270 |
| `color` | `var(--on-primary)` | `[data-palette="cool"] .msg-user .bubble` | 预览资源/css/theme.css:271 |
| `box-shadow` | `0 10px 24px -12px color-mix(in srgb, var(--jade) 55%, transparent)` | `[data-palette="cool"] .msg-user .bubble` | 预览资源/css/theme.css:272 |
| `backdrop-filter` | `none` | `.card, .work-item, .plug-item, .ctx-role, .ctx-sec, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .menu, .usage-pop, .sheet, .island` | 预览资源/css/theme.css:293 |
| `-webkit-backdrop-filter` | `none` | `.card, .work-item, .plug-item, .ctx-role, .ctx-sec, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .menu, .usage-pop, .sheet, .island` | 预览资源/css/theme.css:294 |
| `position` | `relative` | `.work-item, .plug-item, .ctx-role, .ctx-sec, .work-quiet, .qrow, .history-undo, .mcp-row, .hui-section, .file, .plug-page` | 预览资源/css/theme.css:308 |
| `box-shadow` | `inset 0 1px 0 var(--spec-top), inset 0 0 0 1px var(--line), var(--sh-1)` | `.card, .work-item, .plug-item, .ctx-role, .ctx-sec, .dock, .chip` | 预览资源/css/theme.css:318 |
| `box-shadow` | `inset 0 1px 0 var(--spec-top), inset 0 0 0 1px var(--line), var(--sh-2)` | `.composer` | 预览资源/css/theme.css:321 |
| `box-shadow` | `0 0 0 4px var(--jade-soft), inset 0 1px 0 var(--spec-top), inset 0 0 0 1.2px color-mix(in srgb, var(--jade) 52%, transparent), var(--sh-2)` | `.composer:focus-within` | 预览资源/css/theme.css:324 |
| `transform` | `translateY(-2px)` | `.composer:focus-within` | 预览资源/css/theme.css:325 |
| `content` | `""` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:338 |
| `position` | `absolute` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:338 |
| `inset` | `0` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:338 |
| `border-radius` | `inherit` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:338 |
| `padding` | `0` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:339 |
| `border` | `2px solid transparent` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:340 |
| `pointer-events` | `none` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:341 |
| `opacity` | `var(--spot-on, 0)` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:342 |
| `transition` | `opacity var(--d-2)` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:343 |
| `background-image` | `radial-gradient(250px circle at var(--mx, -240px) var(--my, -240px),     color-mix(in srgb, var(--jade) 56%, #f5c451),     color-mix(in srgb, #e5a324 34%, transparent) 52%,     transparent 74%)` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:344 |
| `background-origin` | `border-box` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:349 |
| `background-clip` | `border-area` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:350 |
| `background-repeat` | `no-repeat` | `:is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, .ctx-sec > summary::after` | 预览资源/css/theme.css:351 |
| `background-image` | `radial-gradient(215px circle at var(--mx, -240px) var(--my, -240px),     color-mix(in srgb, var(--jade) 54%, var(--cinnabar)), transparent 72%)` | `[data-palette="cool"] :is(.card, .work-item, .work-quiet, .plug-item, .ctx-role, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page)::after, [data-palette="cool"] .ctx-sec > summary::after` | 预览资源/css/theme.css:355 |
| `position` | `relative` | `[data-palette="cool"] .seal` | 预览资源/css/theme.css:361 |
| `border-radius` | `999px` | `[data-palette="cool"] .seal` | 预览资源/css/theme.css:361 |
| `background` | `transparent` | `[data-palette="cool"] .seal` | 预览资源/css/theme.css:361 |
| `color` | `transparent` | `[data-palette="cool"] .seal` | 预览资源/css/theme.css:362 |
| `font-size` | `0` | `[data-palette="cool"] .seal` | 预览资源/css/theme.css:362 |
| `transform` | `none` | `[data-palette="cool"] .seal` | 预览资源/css/theme.css:362 |
| `animation` | `none` | `[data-palette="cool"] .seal` | 预览资源/css/theme.css:362 |
| `box-shadow` | `inset 0 0 0 1.5px var(--cinnabar)` | `[data-palette="cool"] .seal` | 预览资源/css/theme.css:363 |
| `width` | `40px` | `[data-palette="cool"] .seal` | 预览资源/css/theme.css:365 |
| `height` | `40px` | `[data-palette="cool"] .seal` | 预览资源/css/theme.css:365 |
| `content` | `""` | `[data-palette="cool"] .seal::before` | 预览资源/css/theme.css:367 |
| `position` | `absolute` | `[data-palette="cool"] .seal::before` | 预览资源/css/theme.css:367 |
| `inset` | `0` | `[data-palette="cool"] .seal::before` | 预览资源/css/theme.css:367 |
| `margin` | `auto` | `[data-palette="cool"] .seal::before` | 预览资源/css/theme.css:367 |
| `width` | `6px` | `[data-palette="cool"] .seal::before` | 预览资源/css/theme.css:368 |
| `height` | `6px` | `[data-palette="cool"] .seal::before` | 预览资源/css/theme.css:368 |
| `border-radius` | `50%` | `[data-palette="cool"] .seal::before` | 预览资源/css/theme.css:368 |
| `background` | `var(--cinnabar)` | `[data-palette="cool"] .seal::before` | 预览资源/css/theme.css:368 |
| `content` | `""` | `[data-palette="cool"] .seal::after` | 预览资源/css/theme.css:371 |
| `position` | `absolute` | `[data-palette="cool"] .seal::after` | 预览资源/css/theme.css:371 |
| `inset` | `-4px` | `[data-palette="cool"] .seal::after` | 预览资源/css/theme.css:371 |
| `border-radius` | `999px` | `[data-palette="cool"] .seal::after` | 预览资源/css/theme.css:371 |
| `border` | `1px solid var(--cinnabar)` | `[data-palette="cool"] .seal::after` | 预览资源/css/theme.css:372 |
| `opacity` | `0` | `[data-palette="cool"] .seal::after` | 预览资源/css/theme.css:372 |
| `animation` | `ring-out 2.6s ease-out infinite` | `[data-palette="cool"] .seal::after` | 预览资源/css/theme.css:373 |
| `background` | `linear-gradient(180deg, transparent, var(--cinnabar) 28%, var(--cinnabar) 72%, transparent)` | `[data-palette="cool"] .decide::before` | 预览资源/css/theme.css:376 |
| `transform` | `scale(.72)` | `@keyframes ring-out → 0%` | 预览资源/css/theme.css:379 |
| `opacity` | `.58` | `@keyframes ring-out → 0%` | 预览资源/css/theme.css:379 |
| `transform` | `scale(1.52)` | `@keyframes ring-out → 70%, 100%` | 预览资源/css/theme.css:380 |
| `opacity` | `0` | `@keyframes ring-out → 70%, 100%` | 预览资源/css/theme.css:380 |
| `color` | `var(--on-primary)` | `.send.ready` | 预览资源/css/theme.css:408 |
| `color` | `var(--on-accent)` | `.seal, .req-allow, .opt .tick` | 预览资源/css/theme.css:409 |
| `color` | `var(--on-primary)` | `.summary-card::before, .ctx-summary::before` | 预览资源/css/theme.css:410 |
| `color` | `var(--on-primary)` | `.theme-check` | 预览资源/css/theme.css:411 |
| `color` | `var(--on-primary)` | `.skill .glyph` | 预览资源/css/theme.css:412 |
| `stop-color` | `color-mix(in srgb, var(--jade-hi) 26%, #fff)` | `.og0` | 预览资源/css/theme.css:415 |
| `stop-color` | `var(--jade-hi)` | `.og1` | 预览资源/css/theme.css:416 |
| `stop-color` | `var(--jade)` | `.og2` | 预览资源/css/theme.css:417 |
| `stop-color` | `var(--jade-deep)` | `.og3` | 预览资源/css/theme.css:418 |
| `stop-color` | `color-mix(in srgb, var(--jade) 22%, transparent)` | `.oh0` | 预览资源/css/theme.css:419 |
| `stop-color` | `color-mix(in srgb, var(--cinnabar) 9%, transparent)` | `.oh1` | 预览资源/css/theme.css:420 |
| `display` | `none` | `[data-palette="cool"] .pv-core-jade, [data-palette="cool"] .pv-jade-vernier` | 预览资源/css/theme.css:424 |
| `display` | `none` | `[data-palette="jade"] .pv-core-sphere, [data-palette="jade"] .pv-vernier` | 预览资源/css/theme.css:426 |
| `fill` | `none` | `.pv-jade-vernier` | 预览资源/css/theme.css:429 |
| `stroke` | `color-mix(in srgb, var(--jade) 28%, var(--line-2))` | `.pv-jade-vernier` | 预览资源/css/theme.css:429 |
| `stroke-width` | `.9` | `.pv-jade-vernier` | 预览资源/css/theme.css:430 |
| `stroke-linejoin` | `miter` | `.pv-jade-vernier` | 预览资源/css/theme.css:430 |
| `fill` | `#e6a82e` | `[data-palette="jade"] .pv-pole` | 预览资源/css/theme.css:432 |
| `stroke` | `color-mix(in srgb, #e6a82e 58%, var(--ink-3))` | `[data-palette="jade"] .pv-spindle` | 预览资源/css/theme.css:433 |
| `background` | `radial-gradient(color-mix(in srgb, var(--jade-deep) 36%, transparent), transparent 70%)` | `.orb-shadow` | 预览资源/css/theme.css:435 |
| `background` | `radial-gradient(circle at 33% 28%, color-mix(in srgb, var(--jade-hi) 18%, #fff) 0 7%, var(--jade-hi) 24%, var(--jade) 58%, var(--jade-deep) 100%)` | `.orb-mini` | 预览资源/css/theme.css:437 |
| `box-shadow` | `inset -2px -3px 6px color-mix(in srgb, var(--jade-deep) 45%, transparent),     0 3px 10px -3px color-mix(in srgb, var(--jade) 55%, transparent)` | `.orb-mini` | 预览资源/css/theme.css:438 |
| `border-radius` | `0` | `[data-palette="jade"] .orb-mini` | 预览资源/css/theme.css:442 |
| `background` | `url("../assets/jade-core.webp") center / contain no-repeat` | `[data-palette="jade"] .orb-mini` | 预览资源/css/theme.css:443 |
| `box-shadow` | `none` | `[data-palette="jade"] .orb-mini` | 预览资源/css/theme.css:444 |
| `filter` | `drop-shadow(0 2px 3px rgba(3, 55, 43, .18))` | `[data-palette="jade"] .orb-mini` | 预览资源/css/theme.css:445 |
| `animation` | `breathe 3.8s ease-in-out infinite` | `[data-palette="jade"] .orb-mini` | 预览资源/css/theme.css:446 |
| `content` | `none` | `[data-palette="jade"] .orb-mini::before` | 预览资源/css/theme.css:448 |
| `background` | `conic-gradient(var(--jade), var(--cinnabar), var(--jade))` | `.model-dot` | 预览资源/css/theme.css:449 |
| `display` | `flex` | `.ui-row` | 预览资源/css/theme.css:452 |
| `align-items` | `center` | `.ui-row` | 预览资源/css/theme.css:452 |
| `gap` | `var(--space-5)` | `.ui-row` | 预览资源/css/theme.css:452 |
| `padding` | `var(--space-4) 0` | `.ui-row` | 预览资源/css/theme.css:452 |
| `box-shadow` | `0 -1px 0 var(--line)` | `.ui-row + .ui-row` | 预览资源/css/theme.css:453 |
| `flex` | `1` | `.ui-row > div:first-child` | 预览资源/css/theme.css:454 |
| `min-width` | `0` | `.ui-row > div:first-child` | 预览资源/css/theme.css:454 |
| `display` | `block` | `.ui-row b` | 预览资源/css/theme.css:455 |
| `font-size` | `var(--text-ui)` | `.ui-row b` | 预览资源/css/theme.css:455 |
| `font-weight` | `500` | `.ui-row b` | 预览资源/css/theme.css:455 |
| `display` | `block` | `.ui-row small` | 预览资源/css/theme.css:456 |
| `font-size` | `var(--text-caption)` | `.ui-row small` | 预览资源/css/theme.css:456 |
| `color` | `var(--ink-3)` | `.ui-row small` | 预览资源/css/theme.css:456 |
| `line-height` | `1.55` | `.ui-row small` | 预览资源/css/theme.css:456 |
| `margin-top` | `2px` | `.ui-row small` | 预览资源/css/theme.css:456 |
| `display` | `flex` | `.ui-controls` | 预览资源/css/theme.css:457 |
| `align-items` | `center` | `.ui-controls` | 预览资源/css/theme.css:457 |
| `gap` | `var(--space-3)` | `.ui-controls` | 预览资源/css/theme.css:457 |
| `flex` | `none` | `.ui-controls` | 预览资源/css/theme.css:457 |
| `padding` | `var(--space-1)` | `.ui-seg` | 预览资源/css/theme.css:458 |
| `border-radius` | `var(--r-pill)` | `.ui-seg` | 预览资源/css/theme.css:458 |
| `height` | `28px` | `.ui-seg button` | 预览资源/css/theme.css:459 |
| `padding` | `0 var(--space-4)` | `.ui-seg button` | 预览资源/css/theme.css:459 |
| `border-radius` | `var(--r-pill)` | `.ui-seg button` | 预览资源/css/theme.css:459 |
| `font-size` | `var(--text-ui)` | `.ui-seg button` | 预览资源/css/theme.css:459 |
| `top` | `4px` | `.ui-seg .seg-ink` | 预览资源/css/theme.css:460 |
| `height` | `calc(100% - 8px)` | `.ui-seg .seg-ink` | 预览资源/css/theme.css:460 |
| `border-radius` | `var(--r-pill)` | `.ui-seg .seg-ink` | 预览资源/css/theme.css:460 |
| `display` | `flex` | `.motion-demo` | 预览资源/css/theme.css:461 |
| `align-items` | `center` | `.motion-demo` | 预览资源/css/theme.css:461 |
| `gap` | `5px` | `.motion-demo` | 预览资源/css/theme.css:461 |
| `width` | `34px` | `.motion-demo` | 预览资源/css/theme.css:461 |
| `height` | `24px` | `.motion-demo` | 预览资源/css/theme.css:461 |
| `height` | `28px` | `.ui-controls .motion-play` | 预览资源/css/theme.css:462 |
| `padding` | `0 var(--space-3)` | `.ui-controls .motion-play` | 预览资源/css/theme.css:462 |
| `font-size` | `var(--text-caption)` | `.ui-controls .motion-play` | 预览资源/css/theme.css:462 |
| `border-radius` | `var(--r-pill)` | `.ui-controls .motion-play` | 预览资源/css/theme.css:462 |
| `width` | `8px` | `.motion-demo i` | 预览资源/css/theme.css:463 |
| `height` | `8px` | `.motion-demo i` | 预览资源/css/theme.css:463 |
| `border-radius` | `50%` | `.motion-demo i` | 预览资源/css/theme.css:463 |
| `background` | `var(--jade)` | `.motion-demo i` | 预览资源/css/theme.css:463 |
| `opacity` | `.45` | `.motion-demo i` | 预览资源/css/theme.css:463 |
| `background` | `var(--cinnabar)` | `.motion-demo i:nth-child(2)` | 预览资源/css/theme.css:464 |
| `margin-top` | `var(--space-4)` | `.theme-picker` | 预览资源/css/theme.css:466 |
| `padding-top` | `var(--space-4)` | `.theme-picker` | 预览资源/css/theme.css:466 |
| `box-shadow` | `0 -1px 0 var(--line)` | `.theme-picker` | 预览资源/css/theme.css:466 |
| `display` | `block` | `.theme-copy b` | 预览资源/css/theme.css:467 |
| `font-size` | `var(--text-ui)` | `.theme-copy b` | 预览资源/css/theme.css:467 |
| `font-weight` | `500` | `.theme-copy b` | 预览资源/css/theme.css:467 |
| `display` | `block` | `.theme-copy small` | 预览资源/css/theme.css:468 |
| `font-size` | `var(--text-caption)` | `.theme-copy small` | 预览资源/css/theme.css:468 |
| `color` | `var(--ink-3)` | `.theme-copy small` | 预览资源/css/theme.css:468 |
| `line-height` | `1.55` | `.theme-copy small` | 预览资源/css/theme.css:468 |
| `margin-top` | `2px` | `.theme-copy small` | 预览资源/css/theme.css:468 |
| `display` | `grid` | `.theme-grid` | 预览资源/css/theme.css:469 |
| `grid-template-columns` | `repeat(2, minmax(0, 1fr))` | `.theme-grid` | 预览资源/css/theme.css:469 |
| `gap` | `var(--space-3)` | `.theme-grid` | 预览资源/css/theme.css:469 |
| `margin-top` | `var(--space-3)` | `.theme-grid` | 预览资源/css/theme.css:469 |
| `position` | `relative` | `.theme-card` | 预览资源/css/theme.css:471 |
| `display` | `flex` | `.theme-card` | 预览资源/css/theme.css:471 |
| `align-items` | `center` | `.theme-card` | 预览资源/css/theme.css:471 |
| `gap` | `var(--space-3)` | `.theme-card` | 预览资源/css/theme.css:471 |
| `min-height` | `56px` | `.theme-card` | 预览资源/css/theme.css:472 |
| `text-align` | `left` | `.theme-card` | 预览资源/css/theme.css:472 |
| `padding` | `var(--space-3) var(--space-4)` | `.theme-card` | 预览资源/css/theme.css:472 |
| `border-radius` | `var(--r-ctrl)` | `.theme-card` | 预览资源/css/theme.css:472 |
| `background` | `var(--raised)` | `.theme-card` | 预览资源/css/theme.css:473 |
| `box-shadow` | `inset 0 0 0 1px var(--line)` | `.theme-card` | 预览资源/css/theme.css:473 |
| `transition` | `transform var(--d-2) var(--sp-settle), box-shadow var(--d-2), background var(--d-2)` | `.theme-card` | 预览资源/css/theme.css:474 |
| `transform` | `translateY(-2px)` | `.theme-card:hover` | 预览资源/css/theme.css:476 |
| `background` | `var(--card)` | `.theme-card:hover` | 预览资源/css/theme.css:476 |
| `box-shadow` | `var(--sh-1), inset 0 0 0 1px var(--line-2)` | `.theme-card:hover` | 预览资源/css/theme.css:476 |
| `background` | `var(--card)` | `.theme-card.is-active` | 预览资源/css/theme.css:477 |
| `box-shadow` | `inset 0 0 0 1.8px var(--jade), var(--sh-1)` | `.theme-card.is-active` | 预览资源/css/theme.css:477 |
| `display` | `flex` | `.theme-swatches` | 预览资源/css/theme.css:479 |
| `align-items` | `center` | `.theme-swatches` | 预览资源/css/theme.css:479 |
| `gap` | `var(--space-1)` | `.theme-swatches` | 预览资源/css/theme.css:479 |
| `flex` | `none` | `.theme-swatches` | 预览资源/css/theme.css:479 |
| `width` | `14px` | `.theme-swatches i` | 预览资源/css/theme.css:480 |
| `height` | `14px` | `.theme-swatches i` | 预览资源/css/theme.css:480 |
| `border-radius` | `50%` | `.theme-swatches i` | 预览资源/css/theme.css:480 |
| `box-shadow` | `inset 0 0 0 1px rgba(0, 0, 0, .08)` | `.theme-swatches i` | 预览资源/css/theme.css:480 |
| `display` | `block` | `.theme-name` | 预览资源/css/theme.css:482 |
| `font` | `600 var(--text-body)/1.3 var(--font-sans)` | `.theme-name` | 预览资源/css/theme.css:482 |
| `color` | `var(--ink)` | `.theme-name` | 预览资源/css/theme.css:482 |
| `margin-left` | `auto` | `.theme-check` | 预览资源/css/theme.css:484 |
| `flex` | `none` | `.theme-check` | 预览资源/css/theme.css:484 |
| `width` | `18px` | `.theme-check` | 预览资源/css/theme.css:484 |
| `height` | `18px` | `.theme-check` | 预览资源/css/theme.css:484 |
| `border-radius` | `50%` | `.theme-check` | 预览资源/css/theme.css:484 |
| `display` | `grid` | `.theme-check` | 预览资源/css/theme.css:485 |
| `place-items` | `center` | `.theme-check` | 预览资源/css/theme.css:485 |
| `background` | `var(--jade)` | `.theme-check` | 预览资源/css/theme.css:485 |
| `opacity` | `0` | `.theme-check` | 预览资源/css/theme.css:486 |
| `transform` | `scale(.6)` | `.theme-check` | 预览资源/css/theme.css:486 |
| `transition` | `opacity var(--d-2), transform var(--d-2) var(--sp-bouncy)` | `.theme-check` | 预览资源/css/theme.css:486 |
| `opacity` | `1` | `.theme-card.is-active .theme-check` | 预览资源/css/theme.css:488 |
| `transform` | `none` | `.theme-card.is-active .theme-check` | 预览资源/css/theme.css:488 |
| `flex-direction` | `column` | `@media (max-width: 760px) → .ui-row` | 预览资源/css/theme.css:491 |
| `align-items` | `flex-start` | `@media (max-width: 760px) → .ui-row` | 预览资源/css/theme.css:491 |
| `gap` | `var(--space-3)` | `@media (max-width: 760px) → .ui-row` | 预览资源/css/theme.css:491 |
| `width` | `100%` | `@media (max-width: 760px) → .ui-controls` | 预览资源/css/theme.css:492 |
| `justify-content` | `space-between` | `@media (max-width: 760px) → .ui-controls` | 预览资源/css/theme.css:492 |
| `grid-template-columns` | `1fr` | `@media (max-width: 760px) → .theme-grid` | 预览资源/css/theme.css:493 |
| `margin-top` | `var(--space-3)` | `@media (max-width: 760px) → .theme-picker` | 预览资源/css/theme.css:494 |
| `position` | `fixed` | `.send-ripple` | 预览资源/css/theme.css:499 |
| `z-index` | `75` | `.send-ripple` | 预览资源/css/theme.css:499 |
| `width` | `40px` | `.send-ripple` | 预览资源/css/theme.css:499 |
| `height` | `40px` | `.send-ripple` | 预览资源/css/theme.css:499 |
| `margin` | `-20px 0 0 -20px` | `.send-ripple` | 预览资源/css/theme.css:499 |
| `border-radius` | `50%` | `.send-ripple` | 预览资源/css/theme.css:500 |
| `border` | `1.5px solid var(--jade)` | `.send-ripple` | 预览资源/css/theme.css:500 |
| `pointer-events` | `none` | `.send-ripple` | 预览资源/css/theme.css:500 |
| `position` | `relative` | `.msg.is-new` | 预览资源/css/theme.css:503 |
| `content` | `""` | `.msg.is-new::before` | 预览资源/css/theme.css:505 |
| `position` | `absolute` | `.msg.is-new::before` | 预览资源/css/theme.css:505 |
| `left` | `-12px` | `.msg.is-new::before` | 预览资源/css/theme.css:505 |
| `top` | `4px` | `.msg.is-new::before` | 预览资源/css/theme.css:505 |
| `bottom` | `4px` | `.msg.is-new::before` | 预览资源/css/theme.css:505 |
| `width` | `2px` | `.msg.is-new::before` | 预览资源/css/theme.css:505 |
| `border-radius` | `var(--r-inline)` | `.msg.is-new::before` | 预览资源/css/theme.css:505 |
| `background` | `var(--jade)` | `.msg.is-new::before` | 预览资源/css/theme.css:506 |
| `animation` | `new-rule calc(1.8s * var(--motion)) var(--ease-out) forwards` | `.msg.is-new::before` | 预览资源/css/theme.css:506 |
| `opacity` | `0` | `@keyframes new-rule → 0%` | 预览资源/css/theme.css:509 |
| `transform` | `scaleY(.2)` | `@keyframes new-rule → 0%` | 预览资源/css/theme.css:509 |
| `opacity` | `1` | `@keyframes new-rule → 18%` | 预览资源/css/theme.css:510 |
| `transform` | `none` | `@keyframes new-rule → 18%` | 预览资源/css/theme.css:510 |
| `opacity` | `0` | `@keyframes new-rule → 100%` | 预览资源/css/theme.css:511 |
| `animation` | `send-ready calc(.38s * var(--motion)) var(--sp-bouncy)` | `.send.ready` | 预览资源/css/theme.css:514 |
| `transform` | `scale(.9)` | `@keyframes send-ready → 0%` | 预览资源/css/theme.css:516 |
| `transform` | `scale(1.06)` | `@keyframes send-ready → 60%` | 预览资源/css/theme.css:517 |
| `transform` | `scale(1)` | `@keyframes send-ready → 100%` | 预览资源/css/theme.css:518 |
| `animation` | `chip-in calc(.3s * var(--motion)) var(--sp-bouncy) both` | `.ret-chip button` | 预览资源/css/theme.css:520 |
| `opacity` | `0` | `@keyframes chip-in → from` | 预览资源/css/theme.css:522 |
| `transform` | `translateY(6px) scale(.92)` | `@keyframes chip-in → from` | 预览资源/css/theme.css:522 |
| `opacity` | `1` | `@keyframes chip-in → to` | 预览资源/css/theme.css:523 |
| `transform` | `none` | `@keyframes chip-in → to` | 预览资源/css/theme.css:523 |
| `background` | `var(--raised)` | `.ctx-role` | 预览资源/css/theme.css:527 |
| `box-shadow` | `inset 0 0 0 1px var(--line)` | `.ctx-role` | 预览资源/css/theme.css:527 |
| `border-radius` | `var(--r-ctrl)` | `.ctx-card .ctx-owner` | 预览资源/css/theme.css:528 |
| `box-shadow` | `none` | `.ctx-card .ctx-owner` | 预览资源/css/theme.css:528 |
| `stroke` | `rgba(126, 240, 212, .5)` | `[data-theme="night"][data-palette="jade"] .pv-rim, [data-theme="night"][data-palette="jade"] .pv-jade-vernier` | 预览资源/css/theme.css:531 |
| `filter` | `drop-shadow(0 0 2px rgba(78, 220, 175, .3))` | `[data-theme="night"][data-palette="jade"] .pv-rim, [data-theme="night"][data-palette="jade"] .pv-jade-vernier` | 预览资源/css/theme.css:531 |
| `opacity` | `.66` | `[data-theme="night"][data-palette="jade"] .pv-arc` | 预览资源/css/theme.css:532 |
| `opacity` | `1` | `[data-theme="night"][data-palette="jade"] .pv-arc.front` | 预览资源/css/theme.css:533 |
| `filter` | `drop-shadow(0 0 2px rgba(78, 220, 175, .4))` | `[data-theme="night"][data-palette="jade"] .pv-arc.front` | 预览资源/css/theme.css:533 |
| `background-image` | `radial-gradient(250px circle at 50% var(--my, -240px),     color-mix(in srgb, var(--jade) 56%, var(--cinnabar)),     color-mix(in srgb, var(--cinnabar) 34%, transparent) 52%, transparent 74%)` | `html[data-theme][data-palette] .dock::after` | 预览资源/css/theme.css:537 |
| `background-image` | `radial-gradient(250px circle at 50% var(--my, -240px),     color-mix(in srgb, var(--jade) 56%, #f5c451),     color-mix(in srgb, #e5a324 34%, transparent) 52%, transparent 74%)` | `html[data-theme][data-palette="jade"] .dock::after` | 预览资源/css/theme.css:542 |
| `transition` | `stroke var(--d-3), opacity var(--d-3)` | `[data-palette="jade"] .orb-stage :is(.pv-rim, .pv-jade-vernier, .pv-arc.front)` | 预览资源/css/theme.css:549 |
| `stroke` | `var(--jade)` | `html[data-palette="jade"] .orb-stage.is-grasped :is(.pv-rim, .pv-jade-vernier, .pv-arc.front)` | 预览资源/css/theme.css:552 |
| `opacity` | `.96` | `html[data-palette="jade"] .orb-stage.is-grasped :is(.pv-rim, .pv-jade-vernier, .pv-arc.front)` | 预览资源/css/theme.css:553 |
