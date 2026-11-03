import { defineConfig } from 'astro/config';

// Static output. No integrations, no runtime, no client framework.
// The site is one page plus the boot film, both hand-written.
export default defineConfig({
  output: 'static',
  build: { inlineStylesheets: 'auto' },
  devToolbar: { enabled: false },
});
