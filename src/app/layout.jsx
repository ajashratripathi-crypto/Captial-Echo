import "./globals.css";

export const metadata = {
  title: "Capital Echo",
  description: "Congressional market intelligence",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
