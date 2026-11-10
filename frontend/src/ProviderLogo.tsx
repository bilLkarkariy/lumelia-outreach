/** Text monogram for a provider: third-party logos are deliberately not bundled. */
export function ProviderLogo({provider}: {provider: string}) {
  return (
    <span className={'provider-logo provider-' + provider} aria-hidden="true">
      {provider.slice(0, 2).toUpperCase()}
    </span>
  );
}
