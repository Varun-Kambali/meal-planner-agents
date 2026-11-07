// Hands a file to the browser to save, as a download.

import { localDate } from './dates'

export function saveFile(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.hidden = true
  document.body.append(link)
  try {
    link.click()
  } finally {
    link.remove()
    // The browser has the file once the click has run; the address is not needed after that.
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
}

// Today as YYYY-MM-DD in the reader's own time zone, for a file name.
export function today(): string {
  return localDate(new Date())
}
