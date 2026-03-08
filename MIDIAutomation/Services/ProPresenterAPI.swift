import Foundation
import os.log

private let logger = Logger(subsystem: "com.northwoods.MIDIAutomation", category: "ProPresenterAPI")

/// REST API client for ProPresenter 7 (v7.9+).
/// Fetches playlists, playlist items, and slide text for songs.
final class ProPresenterAPI {
    var host: String
    var port: Int

    private var baseURL: String { "http://\(host):\(port)" }

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 60
        return URLSession(configuration: config)
    }()

    init(host: String = "127.0.0.1", port: Int = 1025) {
        self.host = host
        self.port = port
    }

    // MARK: - Response Models

    struct IDObject: Codable, Hashable {
        let uuid: String
        let name: String
        let index: Int
    }

    struct Playlist: Codable, Identifiable {
        let id: IDObject
        let type: String?
        let children: [Playlist]?

        var uuid: String { id.uuid }
        var name: String { id.name }
    }

    struct PlaylistItem: Codable, Identifiable {
        let id: IDObject
        let type: String?
        let is_hidden: Bool?
        let is_pco: Bool?

        var uuid: String { id.uuid }
        var name: String { id.name }
        var index: Int { id.index }
    }

    struct PresentationResponse: Codable {
        let presentation: PresentationDetail
    }

    struct PresentationDetail: Codable {
        let groups: [SlideGroup]
        let arrangements: [Arrangement]

        enum CodingKeys: String, CodingKey { case groups, arrangements }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            groups = (try? c.decodeIfPresent([SlideGroup].self, forKey: .groups)) ?? []
            arrangements = (try? c.decodeIfPresent([Arrangement].self, forKey: .arrangements)) ?? []
        }
    }

    struct SlideGroup: Codable {
        let uuid: String
        let name: String
        let slides: [Slide]

        enum CodingKeys: String, CodingKey { case uuid, name, slides }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            uuid = (try? c.decodeIfPresent(String.self, forKey: .uuid)) ?? ""
            name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? ""
            slides = (try? c.decodeIfPresent([Slide].self, forKey: .slides)) ?? []
        }
    }

    struct Arrangement: Codable {
        let id: IDObject
        let groups: [String]  // ordered list of group UUIDs (with repeats)

        var name: String { id.name }
        var uuid: String { id.uuid }
    }

    struct Slide: Codable {
        let label: String
        let text: String
        let enabled: Bool

        enum CodingKeys: String, CodingKey { case label, text, enabled }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            label = (try? c.decodeIfPresent(String.self, forKey: .label)) ?? ""
            // Pro7 may return 'text' as a rich-text object — try String first
            text = (try? c.decodeIfPresent(String.self, forKey: .text)) ?? ""
            enabled = (try? c.decodeIfPresent(Bool.self, forKey: .enabled)) ?? true
        }
    }

    // MARK: - Connection

    func checkConnection() async -> Bool {
        guard let url = URL(string: "\(baseURL)/version") else { return false }
        do {
            let (_, response) = try await session.data(from: url)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    // MARK: - Playlists

    /// Get all playlists (flattens any playlist groups).
    func getPlaylists() async throws -> [Playlist] {
        let data = try await get("/v1/playlists")
        let topLevel = try JSONDecoder().decode([Playlist].self, from: data)
        return flattenPlaylists(topLevel)
    }

    /// Flatten playlist groups into a single list of actual playlists.
    private func flattenPlaylists(_ playlists: [Playlist]) -> [Playlist] {
        var result: [Playlist] = []
        for item in playlists {
            if item.type == "group", let children = item.children {
                result.append(contentsOf: flattenPlaylists(children))
            } else {
                result.append(item)
            }
        }
        return result
    }

    /// Get items in a specific playlist.
    func getPlaylistItems(playlistId: String) async throws -> [PlaylistItem] {
        let encoded = playlistId.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? playlistId
        let data = try await get("/v1/playlist/\(encoded)")
        return try JSONDecoder().decode([PlaylistItem].self, from: data)
    }

    // MARK: - Libraries

    /// Pro7 returns library objects with flat uuid, name, index fields.
    struct Library: Codable, Identifiable {
        let uuid: String
        let name: String
        let index: Int

        var id: String { uuid }
    }

    struct LibraryResponse: Codable {
        let items: [LibraryItem]

        enum CodingKeys: String, CodingKey {
            case items
        }
    }

    struct LibraryItem: Codable, Identifiable {
        let uuid: String
        let name: String
        let index: Int

        var id: String { uuid }
    }

    /// Get all libraries.
    func getLibraries() async throws -> [Library] {
        let data = try await get("/v1/libraries")
        return try JSONDecoder().decode([Library].self, from: data)
    }

    /// Get all items in a library.
    func getLibraryItems(libraryId: String) async throws -> [LibraryItem] {
        let encoded = libraryId.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? libraryId
        let data = try await get("/v1/library/\(encoded)")
        let response = try JSONDecoder().decode(LibraryResponse.self, from: data)
        return response.items
    }

    // MARK: - Presentation Slides

    struct PresentationData {
        let groups: [(uuid: String, name: String, slides: [Slide])]
        let arrangements: [Arrangement]
    }

    /// Get all slides from a presentation, organized by group, plus arrangements.
    func getPresentationData(uuid: String) async throws -> PresentationData {
        let encoded = uuid.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? uuid
        let data = try await get("/v1/presentation/\(encoded)")
        let response = try JSONDecoder().decode(PresentationResponse.self, from: data)
        let groups = response.presentation.groups.map { ($0.uuid, $0.name, $0.slides) }
        return PresentationData(groups: groups, arrangements: response.presentation.arrangements)
    }

    /// Get all slides from a presentation, organized by group (legacy convenience).
    func getPresentationSlides(uuid: String) async throws -> [(groupName: String, slides: [Slide])] {
        let presentationData = try await getPresentationData(uuid: uuid)
        return presentationData.groups.map { ($0.name, $0.slides) }
    }

    /// Get flattened slide list from a presentation.
    func getFlatSlides(uuid: String) async throws -> [SlideInfo] {
        let groups = try await getPresentationSlides(uuid: uuid)
        var slides: [SlideInfo] = []
        var index = 0
        for (groupName, groupSlides) in groups {
            for slide in groupSlides {
                // Use label if text is empty (common for song slides)
                let text = slide.text.isEmpty ? slide.label : slide.text
                guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    index += 1
                    continue
                }
                slides.append(SlideInfo(
                    index: index,
                    text: text,
                    groupName: groupName,
                    isEnabled: slide.enabled
                ))
                index += 1
            }
        }
        return slides
    }

    // MARK: - Private

    private func get(_ path: String) async throws -> Data {
        guard let url = URL(string: "\(baseURL)\(path)") else {
            throw APIError.invalidURL
        }
        logger.debug("GET \(url.absoluteString)")
        let (data, response) = try await session.data(from: url)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.requestFailed
        }
        guard httpResponse.statusCode == 200 else {
            logger.error("HTTP \(httpResponse.statusCode) for \(path)")
            throw APIError.httpError(httpResponse.statusCode)
        }
        return data
    }

    enum APIError: Error, LocalizedError {
        case invalidURL
        case requestFailed
        case httpError(Int)

        var errorDescription: String? {
            switch self {
            case .invalidURL: return "Invalid API URL"
            case .requestFailed: return "API request failed"
            case .httpError(let code): return "HTTP error \(code)"
            }
        }
    }
}
