/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_RAYFIN_ENABLE_ENERGY_MAP?: 'true' | 'false'
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare const __APP_VERSION__: string
declare const __BUILD_TIME__: string
declare const __BUILD_COMMIT__: string
