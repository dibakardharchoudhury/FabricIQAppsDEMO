import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteSingleFile } from 'vite-plugin-singlefile'
import { readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8')) as { version: string }

const git = (cmd: string) => { try { return execSync(cmd, { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim() } catch { return '' } }
// Auto-increment the patch from the git commit count so the version bumps on every deploy
// (major.minor stay human-controlled in package.json). Falls back to the package version off-git.
const commitCount = git('git rev-list --count HEAD')
const shortHash = git('git rev-parse --short HEAD')
const [major = '1', minor = '0'] = pkg.version.split('.')
const appVersion = commitCount ? `${major}.${minor}.${commitCount}` : pkg.version

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    viteSingleFile({
      useRecommendedBuildConfig: false,
      inlinePattern: ['**/assets/index-*.js', '**/assets/index-*.css'],
      deleteInlinedFiles: false,
    }),
  ],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
    __BUILD_TIME__: JSON.stringify(new Date().toISOString()),
    __BUILD_COMMIT__: JSON.stringify(shortHash),
  },
})
