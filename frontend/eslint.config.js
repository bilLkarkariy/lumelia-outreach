import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {ignores: ['dist', 'node_modules', 'test-results', 'playwright-report']},
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {globals: globals.browser},
    plugins: {'react-hooks': reactHooks},
    rules: {
      // The bundle is not built with the React Compiler, so the two classic hook rules apply.
      // The compiler-oriented rules of the plugin (set-state-in-effect, refs, purity) flag the
      // data-loading effects and "latest callback" refs used on most screens; they stay off
      // until those screens move to a data-fetching layer.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
    },
  },
  {
    files: ['*.config.{js,ts}'],
    languageOptions: {globals: globals.node},
  },
  {
    // Playwright scripts run in Node and pass callbacks that are evaluated in the page.
    files: ['scripts/**/*.mjs'],
    languageOptions: {globals: {...globals.node, ...globals.browser}},
  },
);
