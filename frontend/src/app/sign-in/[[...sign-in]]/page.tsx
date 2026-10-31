import { authAppearance } from "@/lib/clerkAppearance";
import { SignIn } from "@clerk/nextjs";
import React from "react";

export default function Page(): React.ReactNode {
  return (
    <main className="flex min-h-screen w-full flex-col items-center justify-center gap-10 bg-background">
      <SignIn appearance={authAppearance} />
    </main>
  );
}
