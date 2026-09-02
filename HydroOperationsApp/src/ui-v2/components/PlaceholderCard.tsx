type PlaceholderCardProps = {
  title: string
}

export function PlaceholderCard({ title }: PlaceholderCardProps) {
  return <section className="v2-placeholder-card">
    <span className="v2-eyebrow">Hydro Operations UI v2</span>
    <h1>{title}</h1>
  </section>
}