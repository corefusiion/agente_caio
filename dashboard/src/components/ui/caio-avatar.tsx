import avatarUrl from '../../assets/caio-avatar.png';

/** Decorative: the adjacent text already identifies Caio. */
export function CaioAvatar({ size = 'small' }: { size?: 'small' | 'welcome' }) {
  return (
    <span className={`caio-avatar caio-avatar--${size}`} aria-hidden="true">
      <img src={avatarUrl} alt="" width="1024" height="1024" decoding="async" />
    </span>
  );
}
