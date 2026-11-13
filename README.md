# agb_junction2026

Meta glasses for now

## my-glasses-app

A web app for Meta Ray-Ban Display glasses, built with React, Vite and Meta's
UI Toolkit (`@wearables-ui-toolkit/mrbd`). It is currently a small demo, "Pinch
Counter", that shows how D-pad focus and pinch (select) work.

### Run it locally

Needs Node.js 20.19+ or 22.12+.

```sh
cd my-glasses-app
npm install
npm run dev
```

Open the printed address (usually http://localhost:5173) in Chrome or Brave.

### Test without glasses

Install the [Meta Ray-Ban Display Simulator](https://chromewebstore.google.com/detail/meta-ray-ban-display-simu/jpjlmmodokemlepklkdbimceggpbjcll)
Chrome extension, open the app, and click the extension icon to turn the
simulator on. Click inside the display, then use the arrow keys to move focus
and Enter to pinch.

### Other scripts

- `npm run typecheck`: TypeScript check
- `npm run build`: production build into `dist/`
- `npm run preview`: serve the production build
