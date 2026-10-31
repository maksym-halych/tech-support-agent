import type { Appearance } from "@clerk/ui";

export const authAppearance: Appearance = {
  elements: {
    alternativeMethodsBlockButton:
      "rounded-full bg-primary px-8 min-w-52 hover:bg-primary/90 text-xs py-2 text-gray-200 fill-gray-200",
    backLink: "text-gray-400 hover:text-gray-300",
    // Buttons such as "Can't scan QR code?" and the backup-code
    // download/print/copy icons ship no element descriptor of their own, so
    // they can only be reached through the shared `button` descriptor. The
    // data-attribute prefixes also outrank Clerk's own variant styles.
    button:
      "data-[variant=outline]:border-white/30 data-[variant=ghost]:text-gray-200 data-[variant=ghost]:hover:text-white",
    buttonArrowIcon: "hidden",
    card: "bg-transparent gap-4",
    cardBox: "border-0 shadow-none",
    footer: "hidden",
    footerAction: "hidden",
    form: "items-center w-full",
    formButtonPrimary:
      "rounded-full bg-primary min-w-40 hover:bg-primary/90 text-xs py-2",
    formButtonReset: "text-gray-200 hover:text-white",
    formFieldAction: "text-gray-400 hover:text-gray-300",
    formFieldErrorText: "text-red-400",
    formFieldInput: "rounded-sm checked:bg-gray-600",
    formFieldLabel: "-mb-2 text-xs",
    formFieldRow: "w-80",
    formFieldSuccessText: "text-green-400",
    formFieldWarningText: "text-yellow-400",
    formResendCodeLink: "text-gray-200",
    headerSubtitle: "text-xs",
    headerTitle: "text-2xl font-light mb-1",
    identityPreviewEditButton: "text-gray-300",
    logoBox: "h-14",
    logoImage: "rounded-sm",
    main: "items-center w-full",
    otpCodeFieldInput: "outline-offset-0 text-gray-200 outline-gray-200/25",
  },
  variables: {
    colorForeground: "#f3f4f6",
    colorMutedForeground: "#d1d5db",
    colorNeutral: "white",
  },
};
