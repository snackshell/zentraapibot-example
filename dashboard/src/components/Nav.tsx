"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/orders", label: "Orders" },
  { href: "/customers", label: "Customers" },
  { href: "/deposits", label: "Deposits" },
  { href: "/local-payments", label: "Local Payments" },
  { href: "/credit", label: "Credit by hand" },
  { href: "/settings", label: "Settings" },
];

export default function Nav() {
  const pathname = usePathname();

  return (
    <nav className="nav">
      <div className="nav-brand">
        Zentra<span>API</span> Bot
      </div>
      {LINKS.map((link) => (
        <Link key={link.href} href={link.href} data-active={pathname === link.href}>
          {link.label}
        </Link>
      ))}
      <div className="nav-foot">
        <form action="/api/auth/logout" method="post">
          <button type="submit" className="secondary">Sign out</button>
        </form>
      </div>
    </nav>
  );
}
