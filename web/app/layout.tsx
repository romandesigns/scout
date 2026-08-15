import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "StockHunter Scout",
  description: "StockHunter Scout — bullish momentum detection workstation",
  manifest: "/manifest.webmanifest",
  applicationName: "StockHunter Scout",
  appleWebApp: { capable: true, statusBarStyle: "black-translucent", title: "Scout" },
  icons: { icon: "/icons/scout-192.png", apple: "/icons/scout-192.png" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
