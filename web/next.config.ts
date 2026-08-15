import type { NextConfig } from "next";
import fs from "node:fs";
import path from "node:path";

const versionPaths = [path.resolve(process.cwd(), "../VERSION"), path.resolve(process.cwd(), "VERSION")];
const versionPath = versionPaths.find(candidate => fs.existsSync(candidate));
if (!versionPath) throw new Error(`Scout VERSION was not found. Checked: ${versionPaths.join(", ")}`);
const scoutVersion = fs.readFileSync(versionPath, "utf8").trim();

const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: false,
  env: { NEXT_PUBLIC_SCOUT_VERSION: scoutVersion },
};

export default nextConfig;
