import Foundation

public struct PathHelper {
    public static func resolveProjectRoot(startingAt directory: URL) -> URL {
        var cursor = directory
        let fm = FileManager.default
        
        // Search up to 5 levels for the project root markers
        for _ in 0..<5 {
            let marker = cursor.appendingPathComponent("press_to_talk/core.py")
            let envMarker = cursor.appendingPathComponent(".env")
            
            if fm.fileExists(atPath: marker.path) || fm.fileExists(atPath: envMarker.path) {
                return cursor
            }
            
            let parent = cursor.deletingLastPathComponent()
            if parent.path == cursor.path {
                break
            }
            cursor = parent
        }
        return directory
    }
}
