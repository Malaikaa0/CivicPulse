import { describe, expect, it } from "vitest";
import openapi from "../src/api/openapi.json";
import {
  CATEGORIES,
  COMPLAINT_FIELDS,
  COMPLAINT_PAGE_FIELDS,
  PRIORITIES,
  STATS_FIELDS,
  STATUSES,
} from "../src/api/contract";

type Schema = { properties?: Record<string, unknown>; enum?: string[] };
const schemas = openapi.components.schemas as Record<string, Schema>;

function fieldsOf(name: string): string[] {
  return Object.keys(schemas[name].properties ?? {}).sort();
}

function enumOf(name: string): string[] {
  return [...(schemas[name].enum ?? [])].sort();
}

const keys = (o: object) => Object.keys(o).sort();

describe("frontend API types match the backend's OpenAPI schema", () => {
  it("Complaint has exactly the fields of ComplaintOut", () => {
    expect(keys(COMPLAINT_FIELDS)).toEqual(fieldsOf("ComplaintOut"));
  });

  it("ComplaintPage has exactly the fields of ComplaintPageOut", () => {
    expect(keys(COMPLAINT_PAGE_FIELDS)).toEqual(fieldsOf("ComplaintPageOut"));
  });

  it("Stats has exactly the fields of the Stats schema", () => {
    expect(keys(STATS_FIELDS)).toEqual(fieldsOf("Stats"));
  });

  it("Category, Priority and Status allow exactly the backend's enum values", () => {
    expect(keys(CATEGORIES)).toEqual(enumOf("Category"));
    expect(keys(PRIORITIES)).toEqual(enumOf("Priority"));
    expect(keys(STATUSES)).toEqual(enumOf("Status"));
  });
});
