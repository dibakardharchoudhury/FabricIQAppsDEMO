import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { execSync } from 'node:child_process'

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8')) as { version: string }

const git = (cmd: string) => { try { return execSync(cmd, { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim() } catch { return '' } }
// Auto-increment the patch from the git commit count so the version bumps on every deploy
// (major.minor stay human-controlled in package.json). Falls back to the package version off-git.
const commitCount = git('git rev-list --count HEAD')
const shortHash = git('git rev-parse --short HEAD')
const [major = '1', minor = '0'] = pkg.version.split('.')
const appVersion = commitCount ? `${major}.${minor}.${commitCount}` : pkg.version

function inlineProtectedHostingAssets() {
  return {
    name: 'inline-protected-hosting-assets',
    closeBundle() {
      const output = new URL('./dist/', import.meta.url)
      const htmlUrl = new URL('index.html', output)
      let html = readFileSync(htmlUrl, 'utf-8')
      const filesToDelete: URL[] = []
      html = html.replace(
        /<script type="module" crossorigin src="([^"]+)"><\/script>/,
        (_match, source: string) => {
          const asset = new URL(source.replace(/^\//, ''), output)
          filesToDelete.push(asset)
          const script = readFileSync(asset, 'utf-8').replace(/<\/script/gi, '<\\/script')
          return `<script type="module">${script}</script>`
        },
      )
      html = html.replace(
        /<link rel="stylesheet" crossorigin href="([^"]+)">/g,
        (_match, source: string) => {
          const asset = new URL(source.replace(/^\//, ''), output)
          filesToDelete.push(asset)
          return `<style>${readFileSync(asset, 'utf-8')}</style>`
        },
      )
      writeFileSync(htmlUrl, html)
      for (const asset of filesToDelete) unlinkSync(asset)
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), inlineProtectedHostingAssets()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
    __BUILD_TIME__: JSON.stringify(new Date().toISOString()),
    __BUILD_COMMIT__: JSON.stringify(shortHash),
  },
})
