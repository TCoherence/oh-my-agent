// Registers @testing-library/jest-dom matchers on vitest's `expect` (runtime)
// and augments the Assertion types program-wide (this file is under `src`, so
// `tsc -b` compiles it and the matcher types apply to every test file).
import "@testing-library/jest-dom/vitest";
