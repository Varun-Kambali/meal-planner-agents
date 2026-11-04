// The shell the screens go into (plan, week, log and summary come next).

export function App() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to the content
      </a>
      <main id="main" className="page">
        <h1>Meal planner</h1>
        <section className="notice" aria-labelledby="storage-heading">
          <h2 id="storage-heading">Where your data is kept</h2>
          <p>
            Your plan, its week of recipes and your meal log are kept in this browser only. The
            server keeps none of them. Clearing this browser&apos;s data deletes them, so export a
            copy to keep one.
          </p>
        </section>
      </main>
    </>
  )
}
