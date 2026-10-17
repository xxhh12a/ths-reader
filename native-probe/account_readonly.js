'use strict';

// Audited native account QUERY only. No order submission, cancel, transfer or auth calls.
// xiadan.exe SHA256 is checked by the host before attachment.
if (Process.arch !== 'ia32') throw new Error('This probe only supports the verified 32-bit target');
const target = Process.getModuleByName('xiadan.exe');
const at = va => target.base.add(va - 0x400000);
const guard = (va, expected) => {
  const got = Array.from(new Uint8Array(at(va).readByteArray(expected.length)));
  if (!got.every((b, i) => b === expected[i])) throw new Error('Code guard failed at ' + va.toString(16));
};
guard(0x006d2c30, [0x55,0x8b,0xec,0x6a,0xff,0x68]);
guard(0x005cb1e0, [0x55,0x8b,0xec,0x6a,0xff,0x68]);
guard(0x005c0ce0, [0x55,0x8b,0xec,0x51]);
guard(0x005c0d20, [0x55,0x8b,0xec,0x51]);
guard(0x0118c8f0, [0x55,0x8b,0xec,0x83,0xec,0x08]);
const user32 = Process.getModuleByName('user32.dll');
const threadForWindow = new NativeFunction(user32.getExportByName('GetWindowThreadProcessId'), 'uint', ['pointer','pointer'], 'stdcall');
const postNull = new NativeFunction(user32.getExportByName('PostMessageW'), 'bool', ['pointer','uint','pointer','pointer'], 'stdcall');
const frame = at(0x03547644).readPointer();
if (frame.isNull()) throw new Error('Current MainFrame is not initialized');
const hwnd = frame.add(0x20).readPointer();
const owner = Memory.alloc(4);
const mainThread = threadForWindow(hwnd, owner);
if (mainThread === 0 || owner.readU32() !== Process.id) throw new Error('MainFrame window identity mismatch');

const callOptions = abi => ({abi, scheduling:'exclusive', exceptions:'steal', traps:'none'});
const ctorEmpty = new NativeFunction(at(0x005c0ce0), 'pointer', ['pointer'], callOptions('thiscall'));
const ctorText = new NativeFunction(at(0x005cb1e0), 'pointer', ['pointer','pointer'], callOptions('thiscall'));
const destroy = new NativeFunction(at(0x005c0d20), 'void', ['pointer'], callOptions('thiscall'));
// 0x006ce2e0 pushes exactly its two CString* arguments to this cdecl function.
const queryClientData = new NativeFunction(at(0x006d2c30), 'pointer', ['pointer','pointer'], callOptions('mscdecl'));
const currentAccount = new NativeFunction(at(0x0118c8f0), 'pointer', ['pointer'], callOptions('thiscall'));
const allowedQueries = ['asset','position','order','deal'];

function readCString(object) {
  const data = object.readPointer();
  if (data.isNull()) throw new Error('Null CString data');
  // ATL CStringData: manager, length, capacity, refs precede ANSI data.
  const length = data.sub(12).readS32();
  const capacity = data.sub(8).readS32();
  if (length < 0 || length > 2*1024*1024 || capacity < length) throw new Error('Invalid/beyond-budget CString length');
  return {length, bytes:data.readByteArray(length)};
}

let active = false;
let phase = 'idle';
let listeners = [];
function cleanupHooks() {
  for (const listener of listeners) listener.detach();
  listeners = [];
}

rpc.exports = {
  inspect() {
    return {pid:Process.id, architecture:Process.arch, image_base:target.base.toString(), main_thread:mainThread, main_window:hwnd.toString(), phase,
      backend_rva:'0x2d2c30', allowed_queries:allowedQueries, account_data_read:phase.startsWith('completed_')};
  },
  capture(kind) {
    if (!allowedQueries.includes(kind)) throw new Error('Only audited account QUERY types are allowed');
    const payload = {type:kind};
    if (active) throw new Error('A query is already pending');
    active = true;
    phase = 'waiting_for_main_thread';
    return new Promise((resolve, reject) => {
      let started = false;
      const timer = setTimeout(() => {
        if (!started) {
          cleanupHooks(); active=false; phase='main_thread_timeout';
          reject(new Error('Main thread did not reach an observed message-dispatch boundary'));
        }
      }, 10000);
      const callback = {
        onEnter() {
          if (started || this.threadId !== mainThread) return;
          started = true;
          clearTimeout(timer);
          phase = 'querying_' + kind;
          const input = Memory.alloc(4), output = Memory.alloc(4);
          let inputReady=false, outputReady=false;
          try {
            // Establish allocator, ANSI encoding and lifetime using the target's own CString.
            const encoded = Memory.allocUtf8String(JSON.stringify(payload));
            ctorText(input, encoded); inputReady=true;
            // Hidden return CString storage is constructed by the native callee.
            output.writePointer(ptr(0));
            const inputCheck = readCString(input);
            const inputText = input.readPointer().readUtf8String(inputCheck.length);
            if (inputText !== JSON.stringify(payload)) throw new Error('CString ABI self-check failed');
            const accountBefore = currentAccount(frame);
            if (accountBefore.isNull()) throw new Error('No current account');
            const identityBefore = readCString(accountBefore.add(0x8c9));
            if (identityBefore.length === 0 || identityBefore.length > 256) throw new Error('Invalid current account reference');
            const returned = queryClientData(output, input);
            outputReady=true;
            if (!returned.equals(output)) throw new Error('Native return object does not match the audited ABI');
            const accountAfter = currentAccount(frame);
            if (!accountAfter.equals(accountBefore)) throw new Error('Current account changed during query');
            const identityAfter = readCString(accountAfter.add(0x8c9));
            const beforeBytes = new Uint8Array(identityBefore.bytes), afterBytes = new Uint8Array(identityAfter.bytes);
            if (beforeBytes.length !== afterBytes.length || !beforeBytes.every((b,i)=>b===afterBytes[i])) throw new Error('Current account key changed during query');
            const result = readCString(output);
            // Host stores only the SHA256 reference, never the raw account key.
            send({type:'account_identity',selection_stable:true},identityBefore.bytes);
            send({type:'account_query_result',kind,bytes:result.length,thread_id:this.threadId,source_timestamp:null},result.bytes);
            phase = 'completed_' + kind;
            resolve({kind,bytes:result.length,thread_id:this.threadId,source_timestamp:null});
          } catch (error) {
            phase = 'failed_' + kind;
            reject(error);
          } finally {
            if(outputReady) destroy(output);
            if(inputReady) destroy(input);
            cleanupHooks(); active=false;
          }
        }
      };
      // No UI input. Invoke at the application's normal dispatch boundary, on its own GUI thread.
      listeners = ['DispatchMessageW','DispatchMessageA'].map(name => Interceptor.attach(user32.getExportByName(name), callback));
      Interceptor.flush();
      if (!postNull(hwnd,0,ptr(0),ptr(0))) {
        clearTimeout(timer); cleanupHooks(); active=false; phase='post_null_failed';
        reject(new Error('Could not enqueue a no-op message for the verified main window'));
      }
    });
  }
};
