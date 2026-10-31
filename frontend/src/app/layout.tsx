import type { Metadata } from "next";
import "./globals.css";
import { Roboto } from "next/font/google";
import React from "react";
import { NuqsAdapter } from "nuqs/adapters/next/app";
import { ClerkProvider } from "@clerk/nextjs";
import { enUS } from "@/lib/clerkLocale";

const roboto = Roboto({
  display: "swap",
  subsets: ["latin"],
  weight: ["100", "300", "400", "500", "700"],
});

export const metadata: Metadata = {
  title: "Tech Support Agent",
  description: "Conversational tech support assistant",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={roboto.className}>
        <ClerkProvider
          appearance={{
            elements: {
              avatarBox: {
                height: "3.25rem",
                width: "3.25rem",
              },
            },
            options: { shimmer: false },
          }}
          localization={enUS}
        >
          <NuqsAdapter>{children}</NuqsAdapter>
        </ClerkProvider>
      </body>
    </html>
  );
}
