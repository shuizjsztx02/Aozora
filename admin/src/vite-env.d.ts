/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** admin API 基址覆盖值；缺省时开发态走 127.0.0.1:8011，生产走 /aozora-api。 */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
