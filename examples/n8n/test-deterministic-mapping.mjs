// Run with: node --test examples/n8n/test-deterministic-mapping.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const workflow = JSON.parse(readFileSync(new URL('./fill-pdf-workflow.json', import.meta.url), 'utf8'));
const code = workflow.nodes.find(node => node.name === 'Map values to fields').parameters.jsCode;
const execute = new Function('$input', '$', code);
const fields = [{ id: 'company', name: 'company_name' }, { id: 'ref', name: 'vendor_reference' }, { id: 'email', name: 'remittance_email' }];
const run = (data, pdfFields = fields) => execute(
  { first: () => ({ json: { result: { content: [{ text: JSON.stringify({ fields: pdfFields, summary: { workspace_id: 'synthetic' } }) }] } } }) },
  () => ({ first: () => ({ json: { 'Data JSON': JSON.stringify(data) } }) }),
)[0].json;

test('all synthetic values reach the outgoing fill request unchanged', () => {
  const result = run({ company_name: 'Northwind LLC', vendor_reference: 'V-1042', remittance_email: 'ap@northwind.example' });
  assert.equal(result.matched, 3);
  assert.deepEqual({ ...result.values }, { company: 'Northwind LLC', ref: 'V-1042', email: 'ap@northwind.example' });
  assert.equal(result.fillBody.params.arguments.values, result.values);
});
test('Unicode names survive normalization', () => {
  assert.equal(run({ 'اسم صاحب الطلب': 'اختبار' }, [{ id: 'ar', name: 'اسم صاحب الطلب' }]).values.ar, 'اختبار');
  assert.equal(run({ 'Company Name': 'test' }).values.company, 'test');
});
test('unmatched, partial, empty or ambiguous names stop before filling', () => {
  for (const data of [{ name: 'test' }, { company_name: 'test', typo: 'lost' }, { 'الاسم': 'test' }, { '---': 'test' }]) {
    assert.throws(() => run(data));
  }
  assert.throws(() => run({ company_name: 'test' }, [...fields, { id: 'duplicate', name: 'Company Name' }]), /Ambiguous/);
  assert.throws(() => run({ company_name: 'one', 'Company Name': 'two' }), /same PDF field/);
});
test('rejects non-objects and nested values; accepts explicit scalar and blank values', () => {
  for (const data of [null, [], 1, '', {}, { company_name: [] }, { company_name: {} }]) assert.throws(() => run(data));
  assert.deepEqual({ ...run({ company_name: null, vendor_reference: 0, remittance_email: false }).values }, { company: '', ref: '0', email: 'false' });
});
