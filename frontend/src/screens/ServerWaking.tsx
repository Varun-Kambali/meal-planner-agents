// Said while the demo's free host starts its server (useServerWake). It sits above the screen and
// blocks nothing. An output element is a status region, and this one stays in the page from the
// start, so that a screen reader announces the text when it appears and not on each retry.

import { useServerWake } from '../useServerWake'

export function ServerWaking() {
  const wake = useServerWake()
  return (
    <output>
      {wake === 'starting' && (
        <section className="notice">
          <p className="notice-title">The demo server is starting</p>
          <p>
            The demo's free host stops its server after 15 minutes without use, and starting it
            again takes about a minute. The page works as soon as the server answers.
          </p>
        </section>
      )}
      {wake === 'gave_up' && (
        <section className="notice warning">
          <p className="notice-title">The demo server did not answer</p>
          <p>Try again in a few minutes.</p>
        </section>
      )}
    </output>
  )
}
