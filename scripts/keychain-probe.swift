import Foundation
import Security

let arguments = CommandLine.arguments
guard arguments.count == 3 else {
    exit(2)
}

let query: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: "lupio-device-flow-probe",
    kSecAttrAccount as String: arguments[2],
    kSecAttrSynchronizable as String: false,
]

let status: OSStatus
switch arguments[1] {
case "store":
    let secret = FileHandle.standardInput.readDataToEndOfFile()
    guard !secret.isEmpty else { exit(2) }
    var item = query
    item[kSecValueData as String] = secret
    status = SecItemAdd(item as CFDictionary, nil)
case "read":
    var lookup = query
    lookup[kSecReturnData as String] = true
    lookup[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    status = SecItemCopyMatching(lookup as CFDictionary, &result)
    if status == errSecSuccess {
        guard let secret = result as? Data else { exit(2) }
        FileHandle.standardOutput.write(secret)
    }
case "delete":
    status = SecItemDelete(query as CFDictionary)
default:
    exit(2)
}

if status != errSecSuccess {
    FileHandle.standardError.write(Data("Keychain OSStatus \(status)\n".utf8))
    exit(1)
}
