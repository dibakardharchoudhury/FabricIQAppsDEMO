import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    // The hosted backend's source logic (including work-order approval) lives in these Node modules; an undefined
    // variable there only fails at runtime, after a write may already have happened.
    files: ['scripts/**/*.mjs'],
    languageOptions: { globals: globals.node, sourceType: 'module' },
    rules: { 'no-undef': 'error' },
  },
])
