type PlaceholderPageProps = {
  eyebrow: string;
  title: string;
  description: string;
};

export function PlaceholderPage({
  eyebrow,
  title,
  description,
}: PlaceholderPageProps) {
  return (
    <section className="max-w-2xl space-y-4">
      <p className="text-sm font-semibold uppercase tracking-[0.2em] text-blue-600">
        {eyebrow}
      </p>
      <h1 className="text-4xl font-semibold tracking-tight text-stone-900">
        {title}
      </h1>
      <p className="text-lg leading-8 text-stone-600">{description}</p>
    </section>
  );
}
