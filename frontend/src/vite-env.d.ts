interface ImportMetaEnv {
  // Where the API is, such as https://api.example.org. Empty means the page's own origin, which
  // is how the dev server's proxy reaches `make run-api`.
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
