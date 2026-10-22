import nextJest from "next/jest.js";

// next/jest wires up SWC for TypeScript/JSX and the @/ path alias from tsconfig.
const createJestConfig = nextJest({ dir: "./" });

export default createJestConfig({
  testEnvironment: "jsdom",
  setupFilesAfterEnv: ["<rootDir>/jest.setup.ts"],
});
