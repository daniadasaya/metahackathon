# Katalog Kilat interface

The interface is a phone-first catalog workflow. A compact top bar identifies the current step, with a back action after upload, five progress segments, and a settings sheet for language and appearance. A scrolling content area and a single bottom action area carry each step.

The system font stack is used throughout. Light appearance uses a white canvas, cool gray grouped surfaces, dark text, and an indigo primary action. Dark appearance uses a near-black canvas, lighter grouped surfaces, and a light indigo action. The bolt wordmark uses neutral foreground and canvas colors; accent is reserved for primary actions, selected states, progress, and focus. Success, warning, and error colors carry status; their accompanying text and icons do not rely on color alone. Appearance, type, spacing, control dimensions, and radii are defined in `src/styles/tokens.css`.

Upload combines a photo picker with sample-pack images. Progress uses a labeled five-row timeline with connected status circles. Review keeps product names, prices, photo grouping, and inclusion controls together; its blocker summary opens a list of products that still need prices. Preview shows a store grid with a product detail sheet. Export groups store fields, downloads, product links, and a Commerce Manager checklist.

The app uses 16-pixel page gutters, rounded grouped surfaces, at least 44-pixel interactive controls, and safe-area padding. Product name fields grow with their content and available width. On wider viewports the same app tree is displayed inside a centered phone frame; the phone screen is the scroll container.

The browser uses actual uploaded or sample-pack photos.
