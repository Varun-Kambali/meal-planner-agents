// Said while the demo's free host starts its server (useServerWake). It sits above the screen and
// blocks nothing. An output element is a status region, and this one stays in the page from the
// start, so that a screen reader announces the text when it appears and not on each retry. An
// output element holds phrasing content only, so the notice is spans that the stylesheet makes blocks.

import { useServerWake } from '../useServerWake'

export function ServerWaking() {
  const wake = useServerWake()
  return (
    <output>
      {wake === 'starting' && (
        <span className="notice">
          <span className="notice-title">The demo server is starting</span>
          <span>
            The demo's free host stops its server after 15 minutes without use, and starting it
            again takes about a minute. The page works as soon as the server answers.
          </span>
        </span>
      )}
      {wake === 'gave_up' && (
        <span className="notice warning">
          <span className="notice-title">The demo server did not answer</span>
          <span>Try again in a few minutes.</span>
        </span>
      )}
    </output>
  )
}
