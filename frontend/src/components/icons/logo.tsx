import { Headset, type LucideProps } from "lucide-react";

export const APP_NAME = "Tech Support Agent";

/** The app's mark. Swap the icon here to rebrand every screen at once. */
export function AppLogo(props: LucideProps) {
  return (
    <Headset
      aria-hidden
      {...props}
    />
  );
}
