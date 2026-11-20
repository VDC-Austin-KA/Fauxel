/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_FAUXEL_MOCK?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
